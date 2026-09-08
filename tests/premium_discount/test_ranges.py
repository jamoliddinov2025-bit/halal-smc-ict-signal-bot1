from decimal import Decimal

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.premium_discount import PDClassification, RangeStatus
from tests.analysis.helpers import bar, mirrored, series
from tests.premium_discount.helpers import golden, run


def test_first_range_appears_only_when_both_opposing_swings_are_confirmed():
    frames = run(golden())
    assert all(f.classification == PDClassification.INSUFFICIENT_CONTEXT for f in frames[:3])
    assert frames[2].latest_high.swing.pivot_index == 1 and frames[2].latest_low is None
    assert frames[3].range_status == RangeStatus.CONFIRMED
    r = frames[3].dealing_range
    assert (r.lower_boundary, r.upper_boundary, r.size) == (Decimal(11), Decimal(16), Decimal(5))
    assert r.high_swing.swing.pivot_index == 1 and r.low_swing.swing.pivot_index == 2
    assert r.direction == TrendDirection.BEARISH
    assert r.confirmation_index == 3
    assert r.available_at == frames[3].observation.reference.closed_at
    assert r.confirmation_timestamp == frames[3].observation.reference.opened_at


def test_newer_confirmed_high_after_low_defines_bullish_range():
    frames = run(golden())
    r = frames[4].dealing_range
    assert r.direction == TrendDirection.BULLISH
    assert r.low_swing.swing.pivot_index == 2 and r.high_swing.swing.pivot_index == 3
    assert r.low_swing is frames[3].latest_low
    assert r.high_swing is frames[4].latest_high
    assert r.provenance.evidence_id != frames[3].dealing_range.provenance.evidence_id


def test_endpoint_prices_are_not_relabelled_by_current_trend():
    frames = run(golden())
    # A ranging Phase 3 trend can still have a valid chronologically oriented pair.
    assert (
        frames[3].upstream.upstream.upstream.liquidity.context.snapshot.trend.direction
        == TrendDirection.RANGING
    )
    assert frames[3].dealing_range.direction == TrendDirection.BEARISH


def test_no_new_confirmation_reuses_exact_frozen_range_and_equilibrium():
    candles = (*golden()[:5], bar(5, 10, high=11, low=9))
    frames = run(candles)
    assert frames[5].dealing_range is frames[4].dealing_range
    assert frames[5].equilibrium is frames[4].equilibrium
    assert frames[5].classification == PDClassification.OUTSIDE_RANGE
    assert frames[4].classification == PDClassification.DISCOUNT


def test_outside_candle_confirming_both_extrema_has_no_invented_pivot_order():
    frames = run((bar(0, 10), bar(1, 10, high=15, low=5), bar(2, 10)))
    assert frames[2].latest_high.swing.pivot_index == frames[2].latest_low.swing.pivot_index == 1
    assert frames[2].range_status == RangeStatus.SAME_PIVOT
    assert frames[2].dealing_range is None and frames[2].equilibrium is None
    assert frames[2].classification == PDClassification.INSUFFICIENT_CONTEXT


def test_ambiguous_newest_pair_does_not_silently_fall_back_to_old_range():
    candles = (*golden()[:5], bar(5, "13.5", high=20, low=5), bar(6, 12, high=14, low=10))
    frames = run(candles)
    old = frames[4].dealing_range
    assert old is not None
    assert frames[6].range_status == RangeStatus.SAME_PIVOT
    assert frames[6].dealing_range is None
    assert old.high_swing.swing.pivot_index == 3


def test_nonpositive_latest_pair_yields_insufficient_without_swapping_or_fallback():
    frames = run(series([20, 25, 20, 23, 17, 12, 12, 13, 11]))
    assert frames[4].dealing_range is not None
    assert frames[8].latest_high.swing.price == 14 and frames[8].latest_low.swing.price == 19
    assert frames[8].range_status == RangeStatus.NONPOSITIVE_SPAN
    assert frames[8].classification == PDClassification.INSUFFICIENT_CONTEXT
    assert frames[8].dealing_range is None


def test_pending_tail_cannot_supply_a_range_endpoint():
    first = run(golden()[:2])
    extended = run(golden()[:3])
    assert first == extended[:2]
    assert first[1].latest_high is None and extended[2].latest_high is not None


def test_mirror_reverses_range_orientation_with_exact_endpoint_prices():
    original = run(golden())
    inverse = run(mirrored(golden()))
    for a, b in zip(original, inverse, strict=True):
        assert (a.dealing_range is None) == (b.dealing_range is None)
        if a.dealing_range is not None:
            assert a.dealing_range.direction != b.dealing_range.direction
            assert a.dealing_range.lower_boundary + b.dealing_range.upper_boundary == 1000
            assert a.dealing_range.upper_boundary + b.dealing_range.lower_boundary == 1000


def test_same_numeric_endpoint_price_with_new_pivot_has_new_evidence_not_mutation():
    frames = run(golden())
    earlier, later = frames[3].dealing_range, frames[5].dealing_range
    assert (earlier.lower_boundary, earlier.upper_boundary) == (
        later.lower_boundary,
        later.upper_boundary,
    )
    assert earlier.range_id != later.range_id
    assert earlier.low_swing.swing.pivot_index == 2 and later.low_swing.swing.pivot_index == 4


def test_equal_endpoint_prices_are_not_a_zero_width_dealing_range():
    frames = run(series([20, 25, 20, 23, 20, 17, 17, 18, 16]))
    assert frames[8].latest_high.swing.price == frames[8].latest_low.swing.price == 19
    assert frames[8].range_status == RangeStatus.NONPOSITIVE_SPAN
    assert frames[8].classification == PDClassification.INSUFFICIENT_CONTEXT


def test_single_confirmed_low_without_high_is_insufficient():
    frame = run(series([15, 10, 12]))[2]
    assert frame.latest_low is not None and frame.latest_high is None
    assert frame.classification == PDClassification.INSUFFICIENT_CONTEXT
