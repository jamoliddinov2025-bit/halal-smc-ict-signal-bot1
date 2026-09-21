"""Phase 35C: gap-aware live market feed — additive wrapper over frozen feed.

Wraps the frozen Phase 33 LiveMarketFeed to add:

- Internal contiguity validation of each fetched batch
- Continuity validation vs last_seen cursor
- Preservation of Retry-After from ProviderHTTPError/RateLimitError

It subclasses LiveMarketFeed so existing isinstance checks in LiveService pass.
No second HTTP implementation, no pagination, no threads, no scheduler sleep
hidden — rate-limit errors are not retried inside the feed (per provider
contract "no auto-retry" for 418/429), they are raised immediately with parsed
retry delay preserved.

Gap detection uses existing timeframe_seconds primitive via gap.py.

The feed remains deterministic and IO-free aside from the injected transport.
"""

from __future__ import annotations

from collections.abc import Sequence

from smcsignal.data.config import MarketDataConfig
from smcsignal.data.errors import (
    DataProviderError,
    DataValidationError,
    ProviderHTTPError,
    RateLimitError,
)
from smcsignal.data.models import OHLCVBatch
from smcsignal.live.gap import LiveGapError, detect_continuity_gap, detect_internal_gap
from smcsignal.live.market_feed import LiveMarketFeed
from smcsignal.live.retry_after import RateLimitedFeedError, parse_retry_after


class GapAwareLiveMarketFeed(LiveMarketFeed):
    """LiveMarketFeed subclass that detects gaps and preserves Retry-After.

    - Internal gap within a fetched batch → LiveGapError
    - Continuity gap vs cursor last_seen → LiveGapError
    - Rate-limit (429/418) → RateLimitedFeedError with retry_after_seconds
    - Other ProviderHTTPError with Retry-After → RateLimitedFeedError
    - Other DataProviderError → retried per parent policy, then LiveFeedError
    - DataValidationError → never retried (parent behavior preserved)
    """

    def __init__(
        self,
        config: MarketDataConfig,
        *,
        higher_timeframes: Sequence[str] = (),
        transport=None,
        clock=None,
        sleep_fn=None,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            config,
            higher_timeframes=higher_timeframes,
            transport=transport,
            clock=clock,
            sleep_fn=sleep_fn,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    def _fetch_with_retry(self, provider) -> OHLCVBatch:
        """Override to preserve Retry-After and not retry rate-limit errors."""

        last: Exception | None = None
        # Determine clock for HTTP-date parsing — use provider's clock if available,
        # else UTC now via parse_retry_after's default
        clock_fn = getattr(provider, "_clock", None)
        if callable(clock_fn):
            try:
                now_for_retry = clock_fn()
            except Exception:
                now_for_retry = None
        else:
            now_for_retry = None

        for attempt in range(self._retry_attempts):
            try:
                return provider.fetch_ohlcv()
            except DataValidationError:
                raise
            except RateLimitError as exc:
                # No auto-retry per provider contract
                retry_after_str = getattr(exc, "retry_after", None)
                parsed = parse_retry_after(retry_after_str, now=now_for_retry)
                raise RateLimitedFeedError(
                    status_code=exc.status_code,
                    retry_after=retry_after_str,
                    retry_after_seconds=parsed,
                ) from exc
            except ProviderHTTPError as exc:
                # Preserve Retry-After for any HTTP error that carries it
                retry_after_str = getattr(exc, "retry_after", None)
                parsed = parse_retry_after(retry_after_str, now=now_for_retry)
                # For 429/418, treat as rate-limited (no retry)
                if exc.status_code in (429, 418):
                    raise RateLimitedFeedError(
                        status_code=exc.status_code,
                        retry_after=retry_after_str,
                        retry_after_seconds=parsed,
                    ) from exc
                # For other HTTP errors with Retry-After, also preserve but still retry?
                # Safer: if Retry-After present, raise immediately with delay to respect server
                if parsed is not None:
                    raise RateLimitedFeedError(
                        status_code=exc.status_code,
                        retry_after=retry_after_str,
                        retry_after_seconds=parsed,
                    ) from exc
                last = exc
                if attempt + 1 < self._retry_attempts and self._retry_backoff_seconds > 0:
                    self._sleep(self._retry_backoff_seconds)
            except DataProviderError as exc:
                last = exc
                if attempt + 1 < self._retry_attempts and self._retry_backoff_seconds > 0:
                    self._sleep(self._retry_backoff_seconds)

        # Exhausted retries — preserve retry_after if last had it
        if isinstance(last, ProviderHTTPError):
            retry_after_str = getattr(last, "retry_after", None)
            parsed = parse_retry_after(retry_after_str, now=now_for_retry)
            if parsed is not None or retry_after_str is not None:
                # Wrap last failure with retry info
                raise RateLimitedFeedError(
                    status_code=getattr(last, "status_code", 0),
                    retry_after=retry_after_str,
                    retry_after_seconds=parsed,
                ) from last

        # Fall back to parent's LiveFeedError wrapping
        from smcsignal.live.market_feed import LiveFeedError

        raise LiveFeedError(
            f"market data provider failed after {self._retry_attempts} attempts: {last}"
        ) from last

    def _fetch_new(self, timeframe: str):
        """Override to add gap detection after cursor filtering."""

        cursor = self._cursors[timeframe]
        last_seen_before = cursor.last_seen

        # Fetch batch (may raise RateLimitedFeedError or LiveFeedError)
        batch = self._fetch_with_retry(cursor.provider)

        # Internal gap within raw batch (before cursor filtering)
        internal_gap = detect_internal_gap(
            batch.candles,
            timeframe,
            history_limit=self._config.history_limit,
        )
        if internal_gap is not None:
            raise LiveGapError(internal_gap)

        new_candles = cursor.next_candles(batch.candles)

        continuity_gap = detect_continuity_gap(
            last_seen_before,
            new_candles,
            timeframe,
            history_limit=self._config.history_limit,
        )
        if continuity_gap is not None:
            raise LiveGapError(continuity_gap)

        # Internal gap within new_candles themselves
        internal_new_gap = detect_internal_gap(
            new_candles,
            timeframe,
            history_limit=self._config.history_limit,
        )
        if internal_new_gap is not None:
            raise LiveGapError(internal_new_gap)

        return new_candles
