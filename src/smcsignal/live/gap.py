"""Phase 35C: gap detection for the live primary candle window.

Deterministic, IO-free, uses existing timeframe primitives.

Continuity rule (primary):
    expected_next = last_seen + interval
    - first new == expected → contiguous
    - first new > expected → gap
    - first new < expected → duplicate/stale (safe)
    - internal gaps → gap
    - duplicate timestamps → preserved (cursor dedup)
    - malformed/conflicting duplicates → preserved validation behavior

Fresh startup (last_seen None) validates only internal contiguity.
Restart validates continuity between persisted window last and first new.
No ledger/window/delivery mutation occurs on gap — caller raises before
frozen runtime. 1000-candle limit is not paginated; gap requiring
> history_limit still fails closed. GapInfo carries recoverable flag.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from smcsignal.analysis.mtf.timeframes import timeframe_seconds
from smcsignal.data.models import OHLCV
from smcsignal.live.market_feed import LiveFeedError


@dataclass(frozen=True, slots=True)
class GapInfo:
    """Structured description of a detected primary-window gap."""

    timeframe: str
    expected_timestamp: datetime
    actual_timestamp: datetime
    missing_count: int
    interval_seconds: int
    history_limit: int | None = None

    @property
    def recoverable_within_history_limit(self) -> bool:
        if self.history_limit is None:
            return True
        return self.missing_count <= self.history_limit


class LiveGapError(LiveFeedError):
    """Continuity gap in the primary closed-candle window.

    Subclasses LiveFeedError so existing Phase 35A RECOVERABLE_ERRORS
    classification continues to work without modifying the frozen poll loop.
    """

    def __init__(self, gap: GapInfo, message: str | None = None) -> None:
        self.gap = gap
        msg = message or (
            f"gap detected for {gap.timeframe}: "
            f"expected {gap.expected_timestamp.isoformat()} "
            f"but got {gap.actual_timestamp.isoformat()} "
            f"(missing {gap.missing_count})"
        )
        super().__init__(msg)


def _interval_seconds(timeframe: str) -> int:
    return timeframe_seconds(timeframe)


def expected_next_timestamp(last_seen: datetime, timeframe: str) -> datetime:
    """Return last_seen + interval for the given timeframe."""

    interval = _interval_seconds(timeframe)
    return last_seen + timedelta(seconds=interval)


def _compute_missing(
    expected: datetime, actual: datetime, interval_seconds: int
) -> int:
    delta = (actual - expected).total_seconds()
    if delta <= 0:
        return 0
    return math.ceil(delta / interval_seconds)


def detect_continuity_gap(
    last_seen: datetime | None,
    new_candles: Sequence[OHLCV],
    timeframe: str,
    *,
    history_limit: int | None = None,
) -> GapInfo | None:
    """Detect gap between last_seen and first new candle.

    Returns GapInfo if first new timestamp > expected, else None.
    Empty new_candles → no gap (idle).
    last_seen None → no continuity gap (startup).
    """

    if not new_candles or last_seen is None:
        return None

    interval = _interval_seconds(timeframe)
    expected = expected_next_timestamp(last_seen, timeframe)
    actual = new_candles[0].timestamp

    if actual <= expected:
        # Duplicate/stale or contiguous (actual == expected) → not a gap
        return None

    missing = _compute_missing(expected, actual, interval)
    return GapInfo(
        timeframe=timeframe,
        expected_timestamp=expected,
        actual_timestamp=actual,
        missing_count=missing,
        interval_seconds=interval,
        history_limit=history_limit,
    )


def detect_internal_gap(
    candles: Sequence[OHLCV],
    timeframe: str,
    *,
    history_limit: int | None = None,
) -> GapInfo | None:
    """Detect internal chronological gaps within a sorted candle batch.

    Assumes candles sorted ascending by timestamp (as feed does).
    Returns first gap found, else None.
    Duplicate timestamps ignored — handled by cursor dedup elsewhere.
    """

    if len(candles) < 2:
        return None

    interval = _interval_seconds(timeframe)
    sorted_candles = sorted(candles, key=lambda c: c.timestamp)

    for prev, curr in zip(sorted_candles, sorted_candles[1:], strict=False):
        if curr.timestamp == prev.timestamp:
            continue
        expected = prev.timestamp + timedelta(seconds=interval)
        if curr.timestamp == expected:
            continue
        if curr.timestamp < expected:
            # Non-monotonic / stale — not treated as gap here
            continue
        missing = _compute_missing(expected, curr.timestamp, interval)
        return GapInfo(
            timeframe=timeframe,
            expected_timestamp=expected,
            actual_timestamp=curr.timestamp,
            missing_count=missing,
            interval_seconds=interval,
            history_limit=history_limit,
        )
    return None


def validate_batch_contiguity(
    candles: Sequence[OHLCV],
    timeframe: str,
    *,
    history_limit: int | None = None,
) -> GapInfo | None:
    """Validate that a batch itself is internally contiguous (for startup)."""

    return detect_internal_gap(candles, timeframe, history_limit=history_limit)
