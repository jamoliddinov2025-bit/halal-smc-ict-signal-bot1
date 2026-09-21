"""Phase 35C: gap detection unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smcsignal.analysis.mtf.timeframes import timeframe_seconds
from smcsignal.data.models import OHLCV
from smcsignal.live.gap import (
    GapInfo,
    LiveGapError,
    detect_continuity_gap,
    detect_internal_gap,
    expected_next_timestamp,
    validate_batch_contiguity,
)
from tests.backtest.helpers import EIGHT, PRIMARY_PRICES, bars

STEP = timedelta(minutes=15)
TIMEFRAME = "15m"
INTERVAL = timeframe_seconds(TIMEFRAME)


def candle_at(ts: datetime) -> OHLCV:
    return OHLCV(
        timestamp=ts,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1"),
    )


def test_contiguous_candles_no_gap():
    candles = bars(TIMEFRAME, PRIMARY_PRICES[:6], start=EIGHT)
    assert validate_batch_contiguity(candles, TIMEFRAME) is None
    assert detect_internal_gap(candles, TIMEFRAME) is None


def test_one_missing_candle_internal_gap():
    # Create 0,1,3 (missing 2)
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:6], start=EIGHT)
    # Remove index 2
    gapped = (all_candles[0], all_candles[1], all_candles[3], all_candles[4])
    gap = detect_internal_gap(gapped, TIMEFRAME)
    assert gap is not None
    assert gap.missing_count == 1
    assert gap.timeframe == TIMEFRAME
    assert isinstance(gap, GapInfo)


def test_multiple_missing_candles():
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:10], start=EIGHT)
    # Keep 0,1,4,5 (missing 2,3)
    gapped = (all_candles[0], all_candles[1], all_candles[4], all_candles[5])
    gap = detect_internal_gap(gapped, TIMEFRAME)
    assert gap is not None
    assert gap.missing_count == 2


def test_internal_gap_detection():
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:6], start=EIGHT)
    gapped = (all_candles[0], all_candles[2])  # missing 1
    gap = validate_batch_contiguity(gapped, TIMEFRAME)
    assert gap is not None
    assert gap.expected_timestamp == all_candles[1].timestamp
    assert gap.actual_timestamp == all_candles[2].timestamp


def test_duplicate_timestamp_not_gap():
    c0 = candle_at(EIGHT)
    c1 = candle_at(EIGHT)  # duplicate same timestamp
    c2 = candle_at(EIGHT + STEP)
    # Duplicate should not be considered gap
    gap = detect_internal_gap((c0, c1, c2), TIMEFRAME)
    assert gap is None


def test_stale_non_monotonic_not_gap_as_internal():
    # Out of order: 0,2,1 sorted is contiguous
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:4], start=EIGHT)
    out_of_order = (
        all_candles[2],
        all_candles[0],
        all_candles[1],
        all_candles[3],
    )
    gap = detect_internal_gap(out_of_order, TIMEFRAME)
    assert gap is None


def test_malformed_candle_handling_preserved():
    # Malformed is not gap detector's responsibility — it validates OHLCV already
    # But ensure gap detector doesn't crash on empty
    assert detect_internal_gap((), TIMEFRAME) is None
    assert detect_internal_gap((candle_at(EIGHT),), TIMEFRAME) is None


def test_fresh_startup_contiguous_history():
    candles = bars(TIMEFRAME, PRIMARY_PRICES[:20], start=EIGHT)
    gap = validate_batch_contiguity(candles, TIMEFRAME)
    assert gap is None


def test_fresh_startup_internal_gap():
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:10], start=EIGHT)
    gapped = tuple(c for i, c in enumerate(all_candles) if i != 5)
    gap = validate_batch_contiguity(gapped, TIMEFRAME)
    assert gap is not None
    assert isinstance(gap, GapInfo)


def test_restart_contiguous_continuation():
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:10], start=EIGHT)
    last_seen = all_candles[5].timestamp
    new = all_candles[6:8]  # contiguous
    gap = detect_continuity_gap(last_seen, new, TIMEFRAME)
    assert gap is None


def test_restart_one_missing_candle():
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:10], start=EIGHT)
    last_seen = all_candles[5].timestamp
    new = all_candles[7:9]  # missing 6
    gap = detect_continuity_gap(last_seen, new, TIMEFRAME)
    assert gap is not None
    assert gap.missing_count == 1
    assert gap.expected_timestamp == all_candles[6].timestamp


def test_restart_multiple_missing():
    all_candles = bars(TIMEFRAME, PRIMARY_PRICES[:15], start=EIGHT)
    last_seen = all_candles[5].timestamp
    new = all_candles[9:11]  # missing 6,7,8
    gap = detect_continuity_gap(last_seen, new, TIMEFRAME)
    assert gap is not None
    assert gap.missing_count == 3


def test_restart_requiring_over_1000_candles():
    # Simulate history_limit 100, but gap 150 missing
    last = EIGHT
    actual = EIGHT + timedelta(seconds=INTERVAL * 151)  # 150 missing + 1 expected
    new = (candle_at(actual),)
    gap = detect_continuity_gap(last, new, TIMEFRAME, history_limit=100)
    assert gap is not None
    assert gap.missing_count == 150
    assert gap.recoverable_within_history_limit is False
    assert gap.history_limit == 100


def test_no_new_candle_idle():
    last = EIGHT
    gap = detect_continuity_gap(last, (), TIMEFRAME)
    assert gap is None


def test_forward_clock_skew():
    # Forward skew: last_seen is older than expected due to clock jump?
    # Actually forward skew means now jumps forward, provider may skip?
    # Gap detection should still catch missing
    last = EIGHT
    # Expected next is EIGHT+15m, but actual is EIGHT+30m (one missing)
    actual = EIGHT + timedelta(seconds=INTERVAL * 2)
    new = (candle_at(actual),)
    gap = detect_continuity_gap(last, new, TIMEFRAME)
    assert gap is not None
    assert gap.missing_count == 1


def test_backward_clock_skew():
    # Backward skew: actual earlier than expected (stale) → not gap
    last = EIGHT + timedelta(seconds=INTERVAL * 5)
    actual = EIGHT + timedelta(seconds=INTERVAL * 2)  # earlier than last
    new = (candle_at(actual),)
    gap = detect_continuity_gap(last, new, TIMEFRAME)
    assert gap is None  # stale, not gap


def test_expected_next_timestamp():
    last = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    expected = expected_next_timestamp(last, "15m")
    assert expected == last + timedelta(seconds=900)
    assert expected == last + timedelta(minutes=15)


def test_gap_info_recoverable_flag():
    gap = GapInfo(
        timeframe="15m",
        expected_timestamp=EIGHT + STEP,
        actual_timestamp=EIGHT + STEP * 3,
        missing_count=2,
        interval_seconds=900,
        history_limit=5,
    )
    assert gap.recoverable_within_history_limit is True
    gap2 = GapInfo(
        timeframe="15m",
        expected_timestamp=EIGHT + STEP,
        actual_timestamp=EIGHT + STEP * 10,
        missing_count=9,
        interval_seconds=900,
        history_limit=5,
    )
    assert gap2.recoverable_within_history_limit is False


def test_live_gap_error_is_live_feed_error():
    from smcsignal.live.market_feed import LiveFeedError

    gap = GapInfo(
        timeframe="15m",
        expected_timestamp=EIGHT,
        actual_timestamp=EIGHT + STEP,
        missing_count=1,
        interval_seconds=900,
    )
    err = LiveGapError(gap)
    assert isinstance(err, LiveFeedError)
    assert err.gap == gap
