from decimal import ROUND_DOWN, Context, Decimal, Inexact, localcontext

import pytest

from smcsignal.analysis.displacement.calculation import RATIO_PRECISION
from tests.analysis.helpers import bar
from tests.displacement.helpers import bull, events, run, warmup


@pytest.mark.parametrize("period", [1, 3, 14, 25])
def test_atr_availability_and_first_eligible_index(period):
    frames = run((*warmup(period), bull(period + 1)), atr_period=period)
    assert all(f.atr_current is None for f in frames[:period])
    assert frames[period].atr_current.period == period
    assert frames[period].atr_current.start_index == 1
    assert frames[period].atr_current.end_index == period
    assert frames[period].atr_current.value == 2
    assert frames[period].atr_reference is None and not frames[period].events
    assert frames[period + 1].atr_reference == frames[period].atr_current
    assert [e.detection_index for e in events(frames)] == [period + 1]


def test_true_ranges_include_both_gap_directions_and_reference_previous_close():
    candles = (
        bar(0, 10, opening=10, high=11, low=9),
        bar(1, 11, opening=10, high=12, low=9),
        bar(2, 14, opening=14, high=15, low=13),
        bar(3, 8, opening=8, high=9, low=7),
        bar(4, 17, opening=8, high=18, low=7),
    )
    frames = run(candles)
    atr = frames[4].atr_reference
    assert atr.true_ranges == (Decimal(3), Decimal(4), Decimal(7))
    assert atr.total_true_range == 14 and atr.period == 3
    with localcontext(Context(prec=RATIO_PRECISION)):
        assert atr.value == Decimal(14) / 3
    assert atr.observations[0].reference.candle_index == 0  # anchor, not TR0
    assert atr.reference_candle.candle_index == 3
    assert frames[4].atr_current.true_ranges == (Decimal(4), Decimal(7), Decimal(11))
    assert frames[4].atr_current.total_true_range == 22


def test_current_bar_cannot_inflate_its_own_atr_reference():
    candidate = bar(4, 140, opening=137, high=150, low=50)
    frame = run((*warmup(), candidate))[-1]
    assert frame.atr_reference.value == 2
    assert frame.atr_current.total_true_range == 104
    assert frame.events  # current-inclusive ATR would incorrectly reject the body of 3
    assert frame.events[0].body_atr_ratio == Decimal("1.5")
    assert (
        frame.events[0].atr_reference.provenance.input_prefix_hash
        != frame.provenance.input_prefix_hash
    )


def test_flat_history_zero_atr_is_not_filled_and_does_not_create_an_event():
    frames = run((*warmup(flat=True), bull()))
    assert frames[3].atr_current.value == 0
    assert frames[4].atr_reference.total_true_range == 0
    assert frames[4].metrics.body_atr_ratio is None and frames[4].metrics.range_atr_ratio is None
    assert not frames[4].events
    assert frames[4].atr_current.value == 1  # actual new TR, usable next candle


def test_flat_candles_at_different_prices_have_nonzero_true_range():
    candles = tuple(
        bar(i, price, opening=price, high=price, low=price)
        for i, price in enumerate([100, 102, 102, 102])
    )
    atr = run(candles)[-1].atr_current
    assert atr.true_ranges == (Decimal(2), Decimal(0), Decimal(0))
    assert atr.total_true_range == 2


@pytest.mark.parametrize(
    "floor,expected", [("0", True), ("1.999999999", True), ("2", False), ("2.000000001", False)]
)
def test_atr_floor_is_strict_and_in_price_units(floor, expected):
    assert bool(events(run((*warmup(), bull()), atr_floor=floor))) == expected


def test_short_history_never_borrows_future_true_ranges():
    assert not events(run((bar(0, 100), bull(1))))


def test_arithmetic_and_ids_ignore_ambient_decimal_precision_rounding_and_inexact_traps():
    candles = (
        *warmup(),
        bar(4, 103, opening=100, high=104, low=99),
        bar(5, 95, opening=103, high=104, low=94),
    )
    expected = run(candles)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        assert run(candles) == expected


def test_subnormal_inexact_ratio_underflow_is_rejected_not_silently_reduced_precision():
    from decimal import MIN_EMIN

    from smcsignal.analysis import AnalysisInputError
    from smcsignal.analysis.displacement.calculation import ratio

    with pytest.raises(AnalysisInputError, match="Decimal range"):
        ratio(Decimal((0, (1,), MIN_EMIN)), Decimal(3))
