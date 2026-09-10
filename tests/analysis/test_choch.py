"""Opposing breaks are CHoCH warnings, not hindsight-driven trend flips."""

from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, StructureEventKind, TrendDirection, analyze
from tests.analysis.helpers import bar, mirrored


def test_both_choch_directions_and_broken_swings(golden_candles):
    found = [
        e
        for s in analyze(golden_candles, AnalysisConfig(3))
        for e in s.events
        if e.kind == StructureEventKind.CHOCH
    ]
    assert [
        (e.candle_index, e.direction, e.trend_before, e.level.pivot_index, e.level.price)
        for e in found
    ] == [
        (8, TrendDirection.BEARISH, TrendDirection.BULLISH, 4, Decimal(13)),
        (13, TrendDirection.BULLISH, TrendDirection.BEARISH, 11, Decimal(15)),
    ]


@pytest.mark.parametrize("mirror", [False, True])
def test_wick_only_opposing_break_is_not_choch(golden_candles, mirror):
    candles = golden_candles[:8] + (bar(8, 14, low=10, high=18),)
    candles = mirrored(candles) if mirror else candles
    assert not any(
        e.kind == StructureEventKind.CHOCH
        for s in analyze(candles, AnalysisConfig(3))
        for e in s.events
    )


@pytest.mark.parametrize("mirror", [False, True])
def test_equality_then_opposing_cross(golden_candles, mirror):
    candles = golden_candles[:8] + (bar(8, 13, high=18, low=12), bar(9, 12, high=17, low=11))
    candles = mirrored(candles) if mirror else candles
    result = analyze(candles, AnalysisConfig(3))
    assert result[8].events == ()
    (event,) = result[9].events
    assert event.kind == StructureEventKind.CHOCH
    assert event.previous_close == event.level.price
    assert event.direction != event.trend_before


@pytest.mark.parametrize("mirror", [False, True])
def test_choch_level_is_consumed_on_first_close_cross(golden_candles, mirror):
    candles = golden_candles[:8] + (
        bar(8, 12, high=18, low=10),
        bar(9, 14, high=18, low=9),
        bar(10, 11, high=18, low=8),
    )
    candles = mirrored(candles) if mirror else candles
    found = [
        e
        for s in analyze(candles, AnalysisConfig(3))
        for e in s.events
        if e.kind == StructureEventKind.CHOCH
    ]
    assert len(found) == 1 and found[0].candle_index == 8


def test_no_choch_is_inferred_from_future_direction(golden_candles):
    result = analyze(golden_candles, AnalysisConfig(3))
    assert result[3].trend.direction == TrendDirection.RANGING
    assert result[3].events == ()
    assert result[8].events[0].kind == StructureEventKind.CHOCH
    assert result[8].events[0].trend_before == result[7].trend.direction


def test_bos_and_choch_exhaust_hand_computed_event_sequence(golden_candles):
    result = analyze(golden_candles, AnalysisConfig(3))
    found = [(e.candle_index, e.kind.value, e.direction.value) for s in result for e in s.events]
    assert found == [
        (6, "BOS", "bullish"),
        (8, "CHoCH", "bearish"),
        (12, "BOS", "bearish"),
        (13, "CHoCH", "bullish"),
    ]
    assert all(len(snapshot.events) <= 1 for snapshot in result)
