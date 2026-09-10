"""Continuation breaks use old confirmed levels and strict closing crosses."""

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, StructureEventKind, TrendDirection, analyze
from tests.analysis.helpers import bar, mirrored


def events(candles):
    return [e for snapshot in analyze(candles, AnalysisConfig(3)) for e in snapshot.events]


def test_bullish_and_bearish_bos_have_hand_computed_levels(golden_candles):
    found = [e for e in events(golden_candles) if e.kind == StructureEventKind.BOS]
    assert [(e.candle_index, e.direction, e.level.pivot_index, e.level.price) for e in found] == [
        (6, TrendDirection.BULLISH, 3, Decimal(19)),
        (12, TrendDirection.BEARISH, 10, Decimal(9)),
    ]
    assert found[0].previous_close == 16 and found[0].close == 20
    assert found[1].previous_close == 14 and found[1].close == 8
    assert all(e.direction == e.trend_before for e in found)


@pytest.mark.parametrize("mirror", [False, True])
def test_wick_only_break_does_not_count_as_bos(golden_candles, mirror):
    candles = golden_candles[:6] + (bar(6, 18, high=30, low=15),)
    candles = mirrored(candles) if mirror else candles
    assert events(candles) == []


@pytest.mark.parametrize("mirror", [False, True])
def test_exact_close_touch_is_not_bos_but_next_strict_cross_is(golden_candles, mirror):
    candles = golden_candles[:6] + (bar(6, 19, high=22, low=15), bar(7, 20, high=23, low=15))
    candles = mirrored(candles) if mirror else candles
    found = events(candles)
    assert len(found) == 1
    assert found[0].candle_index == 7
    assert found[0].kind == StructureEventKind.BOS
    assert found[0].previous_close == found[0].level.price


@pytest.mark.parametrize("mirror", [False, True])
def test_repeated_crossings_of_one_swing_emit_only_once(golden_candles, mirror):
    candles = golden_candles[:6] + (
        bar(6, 20, high=25, low=14),
        bar(7, 18, high=26, low=14),
        bar(8, 21, high=27, low=14),
    )
    candles = mirrored(candles) if mirror else candles
    found = events(candles)
    assert len(found) == 1
    assert found[0].kind == StructureEventKind.BOS and found[0].candle_index == 6


@pytest.mark.parametrize("mirror", [False, True])
def test_current_confirmation_cannot_replace_level_before_cross_check(golden_candles, mirror):
    candles = golden_candles[:6] + (bar(6, 18, high=25, low=15), bar(7, 20, high=24, low=15))
    candles = mirrored(candles) if mirror else candles
    last = analyze(candles, AnalysisConfig(3))[-1]
    assert last.events[0].kind == StructureEventKind.BOS
    assert last.events[0].level.pivot_index == 3
    assert any(s.pivot_index == 6 and s.confirmed_index == 7 for s in last.confirmed_swings)


@pytest.mark.parametrize("mirror", [False, True])
def test_ranging_cross_is_consumed_not_reclassified_after_trend_appears(golden_candles, mirror):
    candles = golden_candles[:5] + (
        bar(5, 20, high=22, low=14),
        bar(6, 18, high=23, low=14),
        bar(7, 21, high=24, low=14),
    )
    candles = mirrored(candles) if mirror else candles
    result = analyze(candles, AnalysisConfig(3))
    assert result[4].trend.direction == TrendDirection.RANGING
    assert result[5].trend.ready and result[5].trend.direction != TrendDirection.RANGING
    assert all(snapshot.events == () for snapshot in result)


def test_prior_trend_not_same_candle_new_trend_classifies_break(golden_candles):
    result = analyze(golden_candles, AnalysisConfig(3))
    assert result[9].trend.direction == TrendDirection.RANGING
    assert result[10].trend.direction == TrendDirection.BEARISH
    assert result[10].events == ()


def test_gap_open_requires_no_assumed_intrabar_path(golden_candles):
    candles = golden_candles[:6] + (bar(6, 22, opening=21, low=20, high=23),)
    event = events(candles)[0]
    assert event.kind == StructureEventKind.BOS
    assert event.previous_close < event.level.price < candles[6].low


def test_bos_price_comparisons_are_exact_decimals(golden_candles):
    candles = golden_candles[:6] + (bar(6, "19.000000000000000000001", low=15, high=21),)
    found = events(candles)
    assert len(found) == 1 and found[0].kind == StructureEventKind.BOS


def test_open_and_volume_do_not_change_close_based_event(golden_candles):
    updated = tuple(replace(c, open=c.low, volume=Decimal("999999")) for c in golden_candles)
    assert analyze(updated, AnalysisConfig(3)) == analyze(golden_candles, AnalysisConfig(3))
