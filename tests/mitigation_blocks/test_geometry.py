from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from smcsignal.analysis.order_blocks import ZoneBasis
from tests.analysis.helpers import bar, mirrored
from tests.mitigation_blocks.helpers import OB, bullish_mitigation, events, run
from tests.order_blocks.helpers import bullish


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize(
    "low,qualifies",
    [
        ("15", False),
        ("15.0000000000000000000000000001", False),
        ("14.9999999999999999999999999999", True),
    ],
)
def test_exact_boundary_and_tick_equivalent_difference(mirror, low, qualifies):
    candles = (*bullish()[:7], bar(7, 16, opening=20, high=21, low=low))
    if mirror:
        with localcontext() as ctx:
            ctx.prec = 100
            candles = mirrored((*bullish()[:7], bar(7, 16, opening=20, high=21, low=low)))
    frame = run(candles)[7]
    assert bool(frame.events) == qualifies


def test_completely_outside_the_zone_is_not_mitigation():
    candles = (*bullish()[:7], bar(7, 20, opening=20, high=21, low=19))
    assert run(candles)[7].events == ()


def test_exact_upper_endpoint_touch_has_zero_overlap():
    candles = (*bullish()[:7], bar(7, 16, opening=20, high=21, low=15))
    assert run(candles)[7].events == ()


def test_exact_lower_endpoint_touch_has_zero_overlap():
    candles = (*bullish()[:7], bar(7, 12, opening=12, high=13, low=11))
    assert run(candles)[7].events == ()


def test_positive_wick_overlap_without_body_overlap_qualifies():
    frames = run(bullish_mitigation())
    event = frames[7].events[0]
    assert event.evidence.wick_overlap
    assert not event.evidence.body_overlap
    assert not event.evidence.full_traversal
    assert not event.evidence.open_inside
    assert not event.evidence.close_inside
    assert event.overlap_lower == 14
    assert event.overlap_upper == 15
    assert event.overlap_size == 1


def test_body_overlap_qualifies():
    candles = (*bullish()[:7], bar(7, 14, opening=16, high=17, low="13.5"))
    event = run(candles)[7].events[0]
    assert event.evidence.body_overlap
    assert event.evidence.close_inside
    assert event.overlap_size == Decimal("1.5")
    assert event.evidence.body_overlap_size > 0


def test_full_traversal_without_close_through_qualifies():
    candles = (*bullish()[:7], bar(7, 16, opening=20, high=21, low=12))
    event = run(candles)[7].events[0]
    assert event.evidence.full_traversal
    assert event.overlap_lower == 13
    assert event.overlap_upper == 15
    assert event.overlap_size == 2


def test_opening_and_closing_inside_qualifies():
    candles = (*bullish()[:7], bar(7, "14.5", opening=14, high=16, low=14))
    event = run(candles)[7].events[0]
    assert event.evidence.open_inside
    assert event.evidence.close_inside
    assert event.evidence.body_overlap


def test_doji_strictly_inside_has_zero_length_overlap_but_is_valid():
    candles = (*bullish()[:7], bar(7, 14, opening=14, high=14, low=14))
    event = run(candles)[7].events[0]
    assert event.overlap_size == 0
    assert event.overlap_lower == event.overlap_upper == 14
    assert event.evidence.open_inside and event.evidence.close_inside


def test_original_body_zone_is_preserved_instead_of_reconstructing_full_wicks():
    frames = run(bullish_mitigation(), order_blocks=replace(OB, zone_basis=ZoneBasis.BODY))
    event = frames[7].events[0]
    assert (event.original_lower_boundary, event.original_upper_boundary) == (14, 15)
    assert event.evidence.original_ob.candidate.candle.low == 13
    assert event.overlap_lower == 14
    assert event.overlap_upper == 15


def test_price_geometry_does_not_use_ambient_decimal_rounding():
    candles = (
        *bullish()[:7],
        bar(7, 16, opening=20, high=21, low="14.9999999999999999999999999999"),
    )
    expected = run(candles)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        assert run(candles) == expected
    assert events(expected)[0].overlap_size == Decimal("1E-28")


@pytest.mark.parametrize("mirror", [False, True])
def test_mirrored_geometry_preserves_interior_overlap_semantics(mirror):
    candles = mirrored(bullish_mitigation()) if mirror else bullish_mitigation()
    event = events(run(candles))[0]
    assert event.overlap_size > 0
    assert event.original_lower_boundary < event.original_upper_boundary
