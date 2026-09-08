from dataclasses import replace
from decimal import ROUND_DOWN, Inexact, localcontext

import pytest

from smcsignal.analysis.breaker_blocks import BreakerRejection
from smcsignal.analysis.order_blocks import ZoneBasis
from tests.analysis.helpers import bar, mirrored
from tests.breaker_blocks.helpers import OB, run
from tests.order_blocks.helpers import bullish, choch


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize(
    "close,qualifies",
    [
        ("13", False),
        ("13.0000000000000000000000000001", False),
        ("12.9999999999999999999999999999", True),
    ],
)
def test_exact_boundary_and_tick_equivalent_difference(mirror, close, qualifies):
    candles = (*bullish()[:7], bar(7, close, opening=22, high=23, low=12))
    if mirror:
        # Build the mirror outside the ambient default precision using a wide exact context.
        with localcontext() as ctx:
            ctx.prec = 100
            candles = mirrored((*bullish()[:7], bar(7, close, opening=22, high=23, low=12)))
    frame = run(candles)[7]
    assert bool(frame.events) == qualifies
    if not qualifies:
        assert frame.evidence == ()


def test_wick_only_touch_does_not_consume_first_close_opportunity():
    candles = (
        *bullish()[:7],
        bar(7, 19, opening=20, high=21, low=12),
        bar(8, 9, opening=19, high=20, low=8),
    )
    frames = run(candles)
    assert frames[7].evidence == () and frames[7].events == ()
    assert frames[8].events
    assert frames[8].events[0].invalidation_index == 8


def test_equal_close_is_not_consumed_and_later_strict_close_can_qualify():
    candles = (
        *bullish()[:7],
        bar(7, 13, opening=20, high=21, low=12),
        bar(8, 9, opening=20, high=21, low=8),
    )
    frames = run(candles)
    assert frames[7].evidence == ()
    assert frames[8].events[0].original_ob_confirmation_index == 6


def test_original_body_zone_is_preserved_instead_of_reconstructing_full_wicks():
    frames = run(
        choch(),
        order_blocks=replace(
            OB,
            zone_basis=ZoneBasis.BODY,
        ),
    )
    event = frames[8].events[0]
    assert (event.lower_boundary, event.upper_boundary) == (14, 15)
    assert event.original_lower_boundary == event.lower_boundary
    assert event.original_upper_boundary == event.upper_boundary
    assert event.evidence.original_ob.candidate.candle.low == 13


def test_price_geometry_and_identity_do_not_use_ambient_decimal_rounding():
    candles = (
        *bullish()[:7],
        bar(7, "12.9999999999999999999999999999", opening=22, high=23, low=12),
    )
    expected = run(candles)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        assert run(candles) == expected


def test_gap_through_can_qualify_only_with_actual_opposing_displacement_and_mss():
    candles = (*bullish()[:7], bar(7, 5, opening=12, high=12, low=4))
    frame = run(candles)[7]
    assert frame.events
    event = frame.events[0]
    assert event.evidence.invalidating_candle.candle.open < event.lower_boundary
    assert event.evidence.previous_candle.candle.close > event.lower_boundary
    assert event.evidence.mss is not None and event.evidence.displacement is not None


def test_gap_only_small_body_is_not_confirmation():
    candles = (*bullish()[:7], bar(7, 10, opening="10.1", high=11, low=9))
    frame = run(candles)[7]
    assert not frame.events
    assert frame.evidence[0].rejection_reason == BreakerRejection.MISSING_DISPLACEMENT
