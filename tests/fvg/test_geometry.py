from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.displacement import DisplacementConfig
from tests.analysis.helpers import bar, mirrored, series
from tests.fvg.helpers import bullish, events, golden, run


@pytest.mark.parametrize("count", [0, 1, 2])
def test_insufficient_candles_never_create_a_gap(count):
    assert not events(run(bullish()[:count]))


def test_exactly_three_candles_create_bullish_gap_only_after_c3_closes():
    frames = run(bullish())
    assert not frames[0].events and not frames[1].events
    (event,) = events(frames)
    assert event.direction == TrendDirection.BULLISH
    assert (event.lower_boundary, event.upper_boundary, event.gap_size) == (102, 103, 1)
    assert event.creation_index == event.detection_index == 2
    assert event.c1.reference.candle_index == 0 and event.c2.reference.candle_index == 1
    assert event.c3.reference.candle_index == 2
    assert event.timestamp == event.c3.reference.opened_at
    assert event.available_at == event.c3.reference.closed_at
    assert event.associated_displacement is None


def test_bearish_three_candle_definition_is_the_exact_mirror():
    (event,) = events(run(mirrored(bullish())))
    assert event.direction == TrendDirection.BEARISH
    assert event.lower_boundary == 897 and event.upper_boundary == 898 and event.gap_size == 1
    assert event.lower_boundary == event.c3.candle.high
    assert event.upper_boundary == event.c1.candle.low


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("last_low", ["102", "101.9"])
def test_outer_boundary_equality_and_overlap_are_not_fvgs(mirror, last_low):
    original = bullish()
    candles = (*original[:2], bar(2, 104, opening=104, high=105, low=last_low))
    assert not events(run(mirrored(candles) if mirror else candles))


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize(
    "minimum,expected", [("0", True), ("0.00000001", True), ("0.000000010000001", False)]
)
def test_one_tick_and_exact_minimum_boundary(mirror, minimum, expected):
    candles = (*bullish()[:2], bar(2, "102.5", opening="102.5", high=103, low="102.00000001"))
    result = events(run(mirrored(candles) if mirror else candles, minimum=minimum))
    assert bool(result) == expected
    if result:
        assert result[0].gap_size == Decimal("0.00000001")


@pytest.mark.parametrize("minimum,expected", [("0.999999", True), ("1", True), ("1.000001", False)])
def test_absolute_minimum_is_inclusive_without_quantizing_prices(minimum, expected):
    assert bool(events(run(bullish(), minimum=minimum))) == expected


def test_large_gap_is_preserved_without_capping_or_clipping():
    (event,) = events(run(series([10, 15, 10000])))
    assert event.lower_boundary == 11 and event.upper_boundary == 9999
    assert event.gap_size == 9988


def test_consecutive_overlapping_gaps_remain_independent_creation_records():
    found = events(run(series([10, 12, 15, 18, 20])))
    assert [e.detection_index for e in found] == [2, 3, 4]
    assert [(e.lower_boundary, e.upper_boundary) for e in found] == [(11, 14), (13, 17), (16, 19)]
    assert len({e.event_id for e in found}) == 3


def test_nested_and_opposing_gaps_are_not_merged_cancelled_or_ranked():
    found = events(run(series([10, 15, 20, 12, 14, 17])))
    assert [
        (e.detection_index, e.direction.value, e.lower_boundary, e.upper_boundary) for e in found
    ] == [
        (2, "bullish", Decimal(11), Decimal(19)),
        (3, "bearish", Decimal(13), Decimal(14)),
        (4, "bearish", Decimal(15), Decimal(19)),
        (5, "bullish", Decimal(13), Decimal(16)),
    ]


@pytest.mark.parametrize(
    "middle",
    [bar(1, 101, opening=101, high=105, low=100), bar(1, 101, opening=101, high=130, low=80)],
)
def test_doji_or_large_wick_middle_candle_is_not_an_undocumented_filter(middle):
    candles = (bullish()[0], middle, bullish()[2])
    (event,) = events(run(candles))
    assert event.gap_size == 1 and event.associated_displacement is None


def test_intrabar_path_or_middle_candle_bridge_is_not_invented():
    candles = (bullish()[0], bar(1, 90, opening=90, high=91, low=89), bullish()[2])
    (event,) = events(run(candles))
    assert event.lower_boundary == 102 and event.upper_boundary == 103
    assert event.c2.candle.high < event.lower_boundary


def test_exact_precision_handles_gap_smaller_than_ambient_significant_digits():
    first = bar(
        0,
        "10000000000000000000000000000",
        opening="10000000000000000000000000000",
        high="10000000000000000000000000000",
        low="10000000000000000000000000000",
    )
    middle = replace(first, timestamp=bar(1, 100).timestamp)
    last = bar(
        2,
        "10000000000000000000000000000.01",
        opening="10000000000000000000000000000.01",
        high="10000000000000000000000000000.01",
        low="10000000000000000000000000000.01",
    )
    expected = run((first, middle, last), minimum="0.01")
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        assert run((first, middle, last), minimum="0.01") == expected
    assert events(expected)[0].gap_size == Decimal("0.01")
    assert not events(run((first, middle, last), minimum="0.0100000000000000000000001"))


def test_default_demo_has_hand_computed_bullish_and_bearish_fvgs():
    found = events(run(golden(), displacement=DisplacementConfig()))
    assert [
        (
            e.detection_index,
            e.direction.value,
            e.lower_boundary,
            e.upper_boundary,
            e.gap_size,
            e.associated_displacement.detection_index,
        )
        for e in found
    ] == [
        (16, "bullish", Decimal(101), Decimal(104), Decimal(3), 15),
        (19, "bearish", Decimal(98), Decimal(106), Decimal(8), 18),
    ]
