"""Phase 33 live market feed tests: cursor policy, retries, and closed candles.

Every case runs against an injected fake ``JsonTransport`` and a fixed clock;
the pytest process itself forbids socket access, so no real network call can
occur on any path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.error import URLError

import pytest

from smcsignal.data.config import MarketDataConfig
from smcsignal.data.errors import DataValidationError
from smcsignal.data.models import OHLCV
from smcsignal.live import LiveFeedError, LiveMarketFeed, first_new, interval_of

DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
STEP_MS = 15 * 60 * 1000
OPEN_MS = 1704067200000  # 2024-01-01T00:00:00Z
NOW = datetime(2024, 1, 2, 2, 0, tzinfo=UTC)  # every payload below closes earlier


def minute(open_index: int) -> int:
    """Open time of the ``open_index``-th 15m candle of 2024-01-02, in ms."""

    return OPEN_MS + DAY_MS + open_index * STEP_MS


def kline(opening_ms: int, step_ms: int) -> list[object]:
    return [
        opening_ms,
        "100.00",
        "110.00",
        "90.00",
        "105.00",
        "10.50",
        opening_ms + step_ms - 1,
        "1050.00",
        7,
        "5",
        "500",
        "0",
    ]


def minutes(*open_indexes: int) -> list[list[object]]:
    return [kline(minute(index), STEP_MS) for index in open_indexes]


def hours(*open_indexes: int) -> list[list[object]]:
    base = OPEN_MS + DAY_MS - 2 * HOUR_MS  # 2024-01-01T22:00
    return [kline(base + index * HOUR_MS, HOUR_MS) for index in open_indexes]


def four_hours(*open_indexes: int) -> list[list[object]]:
    base = OPEN_MS + DAY_MS - 4 * HOUR_MS  # 2024-01-01T20:00
    return [kline(base + index * 4 * HOUR_MS, 4 * HOUR_MS) for index in open_indexes]


class ScriptedTransport:
    """Consumes one scripted response per request per interval; last repeats.

    A response is either a canned kline payload, an exception to raise, or an
    empty list. No network access ever happens.
    """

    def __init__(self, responses: dict[str, list[object]]) -> None:
        self.responses = {interval: list(items) for interval, items in responses.items()}
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> object:
        interval = interval_of(url)
        self.calls.append(interval)
        queue = self.responses.setdefault(interval, [])
        if not queue:
            return []  # an untracked interval behaves like an empty page
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def feed(
    transport: ScriptedTransport,
    *,
    higher: tuple[str, ...] = ("1h",),
    retry_attempts: int = 3,
    sleeps: list[float] | None = None,
) -> LiveMarketFeed:
    recorded = sleeps if sleeps is not None else []
    return LiveMarketFeed(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=100,
        ),
        higher_timeframes=higher,
        transport=transport,
        clock=lambda: NOW,
        sleep_fn=recorded.append,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=1.0,
    )


def opens(candles: tuple[OHLCV, ...]) -> tuple[datetime, ...]:
    return tuple(candle.timestamp for candle in candles)


def test_poll_returns_only_closed_candles() -> None:
    transport = ScriptedTransport(
        {"15m": [minutes(0, 1)], "1h": [hours(0, 1)], "4h": [four_hours(0)]}
    )
    update = feed(transport, higher=("1h", "4h")).poll()
    assert opens(update.primary) == (
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 2, 0, 15, tzinfo=UTC),
    )
    assert opens(update.higher["1h"]) == (
        datetime(2024, 1, 1, 22, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 23, 0, tzinfo=UTC),
    )
    assert opens(update.higher["4h"]) == (datetime(2024, 1, 1, 20, 0, tzinfo=UTC),)


def test_in_progress_candle_never_reaches_the_feed() -> None:
    # The 02:00 candle closes at 02:14:59, after the fixed clock: the provider
    # drops it before the feed sees it.
    transport = ScriptedTransport({"15m": [minutes(0, 2, 8)], "1h": [[]]})
    update = feed(transport).poll()
    assert opens(update.primary) == (
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 2, 0, 30, tzinfo=UTC),
    )


def test_cursor_advances_and_duplicates_are_ignored() -> None:
    transport = ScriptedTransport({"15m": [minutes(0, 1)], "1h": [[]]})
    service = feed(transport)
    assert len(service.poll().primary) == 2
    # Same snapshot again: one candle sits at the cursor, one behind it.
    second = service.poll()
    assert second.primary == ()
    assert second.duplicates_ignored == 1
    assert second.stale_ignored == 1


def test_overlap_poll_returns_only_the_new_candle() -> None:
    transport = ScriptedTransport({"15m": [minutes(0, 1), minutes(0, 1, 2)], "1h": [[]]})
    service = feed(transport)
    assert len(service.poll().primary) == 2
    second = service.poll()
    assert opens(second.primary) == (datetime(2024, 1, 2, 0, 30, tzinfo=UTC),)
    # The two overlap rows: one duplicate at the cursor, one stale behind it.
    assert second.duplicates_ignored == 1
    assert second.stale_ignored == 1


def test_stale_out_of_order_candles_are_ignored_deterministically() -> None:
    transport = ScriptedTransport({"15m": [minutes(1, 2), minutes(2, 0, 4, 3)], "1h": [[]]})
    service = feed(transport)
    assert len(service.poll().primary) == 2
    second = service.poll()
    assert opens(second.primary) == (
        datetime(2024, 1, 2, 0, 45, tzinfo=UTC),
        datetime(2024, 1, 2, 1, 0, tzinfo=UTC),
    )
    # Two rows at/behind the cursor: one duplicate, one stale.
    assert second.duplicates_ignored == 1
    assert second.stale_ignored == 1


def test_higher_timeframes_are_tracked_independently() -> None:
    transport = ScriptedTransport({"15m": [minutes(0)], "1h": [hours(0, 1)], "4h": [four_hours(0)]})
    service = feed(transport, higher=("1h", "4h"))
    assert service.higher_timeframes == ("1h", "4h")
    update = service.poll()
    assert len(update.higher["1h"]) == 2
    assert len(update.higher["4h"]) == 1
    again = service.poll()
    assert again.primary == ()
    assert all(candles == () for candles in again.higher.values())


def test_invalid_higher_timeframes_are_rejected_at_construction() -> None:
    kwargs: dict[str, object] = {"clock": lambda: NOW}
    with pytest.raises(Exception, match="unsupported timeframe"):
        LiveMarketFeed(
            MarketDataConfig(
                symbol="BTCUSDT",
                timeframe="15m",
                data_source="binance_public",
                history_limit=100,
            ),
            higher_timeframes=("20m",),
            transport=ScriptedTransport({}),
            **kwargs,  # type: ignore[arg-type]
        )
    with pytest.raises(Exception, match="cannot equal the primary timeframe"):
        LiveMarketFeed(
            MarketDataConfig(
                symbol="BTCUSDT",
                timeframe="15m",
                data_source="binance_public",
                history_limit=100,
            ),
            higher_timeframes=("15m",),
            transport=ScriptedTransport({}),
            **kwargs,  # type: ignore[arg-type]
        )
    with pytest.raises(Exception, match="duplicate higher timeframe"):
        LiveMarketFeed(
            MarketDataConfig(
                symbol="BTCUSDT",
                timeframe="15m",
                data_source="binance_public",
                history_limit=100,
            ),
            higher_timeframes=("1h", "1h"),
            transport=ScriptedTransport({}),
            **kwargs,  # type: ignore[arg-type]
        )


def test_malformed_data_is_never_retried() -> None:
    # A structurally invalid kline page is a deterministic data error: the
    # feed fails fast with exactly one attempt and no retry backoff.
    transport = ScriptedTransport({"15m": [[["bad", "row"]]]})
    service = feed(transport, higher=("1h",))
    with pytest.raises(DataValidationError):
        service.poll()
    assert transport.calls == ["15m"]


def test_provider_error_objects_exhaust_the_retry_budget() -> None:
    # An error object (dict payload) is a provider failure: retryable, then
    # wrapped as LiveFeedError after the bounded budget.
    transport = ScriptedTransport({"15m": [{"code": -1121, "msg": "Bad symbol"}]})
    sleeps: list[float] = []
    with pytest.raises(LiveFeedError, match="after 3 attempts"):
        feed(transport, higher=("1h",), sleeps=sleeps).poll()
    assert transport.calls.count("15m") == 3
    assert sleeps == [1.0, 1.0]


def test_transient_failure_is_retried_with_injected_backoff() -> None:
    transport = ScriptedTransport({"15m": [URLError("transient"), minutes(0)], "1h": [[]]})
    sleeps: list[float] = []
    update = feed(transport, sleeps=sleeps).poll()
    assert opens(update.primary) == (datetime(2024, 1, 2, 0, 0, tzinfo=UTC),)
    assert transport.calls.count("15m") == 2
    assert sleeps == [1.0]


def test_retry_exhaustion_raises_live_feed_error() -> None:
    transport = ScriptedTransport({"15m": [URLError("down")], "1h": [[]]})
    sleeps: list[float] = []
    with pytest.raises(LiveFeedError, match="after 3 attempts"):
        feed(transport, sleeps=sleeps).poll()
    assert transport.calls.count("15m") == 3
    assert sleeps == [1.0, 1.0]


def test_feed_requires_the_binance_public_provider() -> None:
    csv_config = MarketDataConfig(
        symbol="BTCUSDT",
        timeframe="15m",
        data_source="csv",
        history_limit=10,
        csv_path="/tmp/quotes.csv",
    )
    with pytest.raises(LiveFeedError, match="binance_public"):
        LiveMarketFeed(csv_config, clock=lambda: NOW)


def test_interval_helper_routes_provider_urls() -> None:
    url = (
        "https://data-api.binance.vision/api/v3/klines"
        "?symbol=BTCUSDT&interval=1h&limit=11&endTime=1&timeZone=0"
    )
    assert interval_of(url) == "1h"
    with pytest.raises(ValueError, match="no interval"):
        interval_of("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT")


def test_first_new_helper_matches_the_feed_partition() -> None:
    def candle(at: datetime) -> OHLCV:
        return OHLCV(
            timestamp=at,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("1"),
        )

    a = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    b = a + timedelta(minutes=15)
    c = b + timedelta(minutes=15)
    candles = (candle(b), candle(a), candle(c))
    assert opens(first_new(candles, None)) == (a, b, c)
    assert opens(first_new(candles, b)) == (c,)
    assert first_new((), a) == ()
