"""Phase 33 live market feed: a cursor over the existing public Binance provider.

The feed wraps the frozen Phase 2 :class:`smcsignal.data.BinancePublicDataProvider`
— no second HTTP implementation exists. One poll asks each tracked timeframe's
provider for a bounded snapshot of closed candles (the provider already
excludes the in-progress candle) and projects the snapshot through a per-
timeframe cursor so only *new* closed candles are handed to the caller.

Deterministic cursor policy
---------------------------

For each timeframe, one poll's candles are ordered by opening timestamp and
then partitioned against the cursor:

- candles strictly newer than the cursor are new and advance the cursor;
- candles at or older than the cursor are duplicates/stale and are ignored,
  counted in ``duplicates_ignored`` / ``stale_ignored``;
- chronological gaps are tolerated and never filled, moved, or synthesized.

Transient provider/transport failures are retried a bounded number of times
with an injectable sleep between attempts; deterministically malformed data
(``DataValidationError``) is never retried. Exhausted retries raise
``LiveFeedError``.

The feed contains no SMC/ICT analysis, no signal logic, and no Telegram
logic, and it never mutates candle data. Tests inject a fake transport and a
fake clock; no real network call exists on any tested path.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from smcsignal.analysis.mtf.timeframes import require_higher_multiple
from smcsignal.data.binance import BinancePublicDataProvider, JsonTransport
from smcsignal.data.config import MarketDataConfig
from smcsignal.data.errors import DataProviderError, DataValidationError
from smcsignal.data.models import OHLCV, OHLCVBatch

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0


class LiveFeedError(RuntimeError):
    """The live market feed exhausted its bounded retry budget."""


@dataclass(frozen=True, slots=True)
class LiveFeedUpdate:
    """New closed candles from one poll, plus the deterministic ignore tally."""

    primary: tuple[OHLCV, ...]
    higher: Mapping[str, tuple[OHLCV, ...]]
    duplicates_ignored: int = 0
    stale_ignored: int = 0


@dataclass(slots=True)
class _Cursor:
    """Per-timeframe cursor and ignore tally behind one provider."""

    provider: BinancePublicDataProvider
    last_seen: datetime | None = None
    duplicates_ignored: int = 0
    stale_ignored: int = 0

    def next_candles(self, batch: tuple[OHLCV, ...]) -> tuple[OHLCV, ...]:
        """Partition one ascending snapshot into new candles and ignored ones."""

        fresh: list[OHLCV] = []
        for candle in sorted(batch, key=lambda candle: candle.timestamp):
            if self.last_seen is not None and candle.timestamp <= self.last_seen:
                if candle.timestamp == self.last_seen:
                    self.duplicates_ignored += 1
                else:
                    self.stale_ignored += 1
                continue
            fresh.append(candle)
        if fresh:
            self.last_seen = fresh[-1].timestamp
        return tuple(fresh)


class LiveMarketFeed:
    """Cursor-delimited closed-candle feed over the existing public provider.

    One feed tracks the primary timeframe plus every configured higher
    timeframe, each through its own ``BinancePublicDataProvider`` built on the
    same validated configuration values and the same injected transport and
    clock. ``poll()`` performs at most one provider request per timeframe and
    returns only closed candles the caller has not seen before.
    """

    def __init__(
        self,
        config: MarketDataConfig,
        *,
        higher_timeframes: Sequence[str] = (),
        transport: JsonTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        if not isinstance(config, MarketDataConfig):
            raise LiveFeedError("LiveMarketFeed requires a MarketDataConfig")
        if config.data_source != "binance_public":
            raise LiveFeedError(
                "live polling requires the binance_public market data provider; "
                f"got {config.data_source!r}"
            )
        if type(retry_attempts) is not int or retry_attempts < 1:
            raise LiveFeedError("retry_attempts must be a positive integer")
        if (
            isinstance(retry_backoff_seconds, bool)
            or not isinstance(retry_backoff_seconds, (int, float))
            or retry_backoff_seconds < 0
        ):
            raise LiveFeedError("retry_backoff_seconds must be a nonnegative number")
        higher: list[str] = []
        for timeframe in higher_timeframes:
            if not isinstance(timeframe, str) or not timeframe.strip():
                raise LiveFeedError("higher timeframes must be nonempty strings")
            if timeframe == config.timeframe:
                raise LiveFeedError("a higher timeframe cannot equal the primary timeframe")
            if timeframe in higher:
                raise LiveFeedError(f"duplicate higher timeframe: {timeframe}")
            require_higher_multiple(config.timeframe, timeframe)
            higher.append(timeframe)
        self._config = config
        self._higher_timeframes: tuple[str, ...] = tuple(higher)
        self._transport = transport
        self._clock = clock
        self._sleep = sleep_fn if sleep_fn is not None else _default_sleep
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = float(retry_backoff_seconds)
        self._cursors: dict[str, _Cursor] = {}
        self._cursors[config.timeframe] = self._new_cursor(config)
        for timeframe in higher:
            self._cursors[timeframe] = self._new_cursor(
                MarketDataConfig(
                    symbol=config.symbol,
                    timeframe=timeframe,
                    data_source="binance_public",
                    history_limit=config.history_limit,
                    timeout_seconds=config.timeout_seconds,
                )
            )

    def _new_cursor(self, provider_config: MarketDataConfig) -> _Cursor:
        provider = BinancePublicDataProvider(
            provider_config,
            transport=self._transport,
            clock=self._clock,
        )
        return _Cursor(provider)

    @property
    def config(self) -> MarketDataConfig:
        """The validated primary-timeframe market-data configuration."""

        return self._config

    @property
    def higher_timeframes(self) -> tuple[str, ...]:
        """The tracked higher timeframes, in configured order."""

        return self._higher_timeframes

    def poll(self) -> LiveFeedUpdate:
        """Poll every tracked timeframe once; return only new closed candles."""

        primary = self._fetch_new(self._config.timeframe)
        higher = {timeframe: self._fetch_new(timeframe) for timeframe in self._higher_timeframes}
        duplicates = sum(cursor.duplicates_ignored for cursor in self._cursors.values())
        stale = sum(cursor.stale_ignored for cursor in self._cursors.values())
        return LiveFeedUpdate(
            primary=primary,
            higher=higher,
            duplicates_ignored=duplicates,
            stale_ignored=stale,
        )

    def _fetch_new(self, timeframe: str) -> tuple[OHLCV, ...]:
        cursor = self._cursors[timeframe]
        batch = self._fetch_with_retry(cursor.provider)
        return cursor.next_candles(batch.candles)

    def _fetch_with_retry(self, provider: BinancePublicDataProvider) -> OHLCVBatch:
        last: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                return provider.fetch_ohlcv()
            except DataValidationError:
                # Deterministically malformed data: retrying cannot repair it.
                raise
            except DataProviderError as exc:
                last = exc
                if attempt + 1 < self._retry_attempts and self._retry_backoff_seconds > 0:
                    self._sleep(self._retry_backoff_seconds)
        raise LiveFeedError(
            f"market data provider failed after {self._retry_attempts} attempts: {last}"
        ) from last


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def interval_of(url: str) -> str:
    """Extract the ``interval`` query parameter from one provider request URL.

    Exposed so a deterministic fake transport in tests can route one provider
    URL to one timeframe's canned payload without any networking.
    """

    values = parse_qs(urlparse(url).query).get("interval")
    if not values:
        raise ValueError(f"provider URL carries no interval parameter: {url}")
    return values[0]


def first_new(candles: Iterable[OHLCV], after: datetime | None) -> tuple[OHLCV, ...]:
    """Return the candles strictly newer than ``after``, in ascending order.

    The pure form of the feed's deterministic cursor partition, available to
    callers that must re-derive the same partition over a restored window.
    """

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if after is None:
        return tuple(ordered)
    return tuple(candle for candle in ordered if candle.timestamp > after)
