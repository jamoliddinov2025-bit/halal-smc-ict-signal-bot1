"""Strict confirmed HH/HL and LH/LL trends, including incomplete evidence."""

from dataclasses import replace

import pytest

from smcsignal.analysis import (
    AnalysisConfig,
    AnalysisInputError,
    SwingKind,
    TrendDirection,
    analyze,
    classify_trend,
)
from tests.analysis.helpers import BASE, STEP, swing


def state(highs, lows):
    return classify_trend(
        candle_index=10,
        timestamp=BASE + 10 * STEP,
        swing_highs=tuple(
            swing(SwingKind.HIGH, price, index) for price, index in zip(highs, (1, 5), strict=False)
        ),
        swing_lows=tuple(
            swing(SwingKind.LOW, price, index) for price, index in zip(lows, (3, 7), strict=False)
        ),
    )


@pytest.mark.parametrize(
    ("highs", "lows", "expected"),
    [
        ([100, 110], [80, 90], TrendDirection.BULLISH),
        ([110, 100], [90, 80], TrendDirection.BEARISH),
        ([100, 110], [90, 80], TrendDirection.RANGING),
        ([110, 100], [80, 90], TrendDirection.RANGING),
        ([100, 100], [80, 90], TrendDirection.RANGING),
        ([100, 110], [80, 80], TrendDirection.RANGING),
        ([110, 100], [80, 80], TrendDirection.RANGING),
        ([100, 100], [90, 80], TrendDirection.RANGING),
        ([100, 100], [80, 80], TrendDirection.RANGING),
    ],
)
def test_explicit_two_high_two_low_rules(highs, lows, expected):
    result = state(highs, lows)
    assert result.direction == expected
    assert result.ready is True


@pytest.mark.parametrize(
    ("highs", "lows"),
    [([], []), ([100], []), ([], [80]), ([100], [80]), ([100, 110], [80]), ([100], [80, 90])],
)
def test_warmup_is_ranging_but_not_ready(highs, lows):
    result = state(highs, lows)
    assert result.direction == TrendDirection.RANGING
    assert result.ready is False


def test_trend_changes_only_when_confirmed_evidence_changes(golden_candles):
    result = analyze(golden_candles, AnalysisConfig(3))
    assert [s.trend.direction.value for s in result] == [
        "ranging",
        "ranging",
        "ranging",
        "ranging",
        "ranging",
        "bullish",
        "bullish",
        "bullish",
        "bullish",
        "ranging",
        "bearish",
        "bearish",
        "bearish",
        "bearish",
    ]
    assert [s.trend.ready for s in result] == [False] * 5 + [True] * 9
    for snapshot in result:
        assert len(snapshot.trend.swing_highs) <= 2
        assert len(snapshot.trend.swing_lows) <= 2
        assert all(
            s.confirmed_index <= snapshot.candle_index
            for s in (*snapshot.trend.swing_highs, *snapshot.trend.swing_lows)
        )


def test_choch_does_not_automatically_flip_swing_derived_trend(golden_candles):
    result = analyze(golden_candles, AnalysisConfig(3))
    assert result[8].events[0].direction == TrendDirection.BEARISH
    assert result[8].trend.direction == TrendDirection.BULLISH
    assert result[13].events[0].direction == TrendDirection.BULLISH
    assert result[13].trend.direction == TrendDirection.BEARISH


@pytest.mark.parametrize("by_index", [True, False])
def test_future_confirmations_are_rejected_even_with_complete_price_pairs(by_index):
    highs = (swing(SwingKind.HIGH, 100, 1), swing(SwingKind.HIGH, 110, 5))
    later = (
        replace(highs[1], confirmed_index=11)
        if by_index
        else replace(highs[1], confirmed_timestamp=BASE + 11 * STEP)
    )
    with pytest.raises(AnalysisInputError, match="future"):
        classify_trend(
            candle_index=10,
            timestamp=BASE + 10 * STEP,
            swing_highs=(highs[0], later),
            swing_lows=(swing(SwingKind.LOW, 80, 3), swing(SwingKind.LOW, 90, 7)),
        )


def test_wrong_swing_kinds_are_not_trend_evidence():
    with pytest.raises(AnalysisInputError, match="wrong kind"):
        classify_trend(
            candle_index=10, timestamp=BASE + 10 * STEP, swing_highs=(swing(SwingKind.LOW, 90, 1),)
        )


def test_only_latest_two_pairs_determine_current_trend(golden_candles):
    last = analyze(golden_candles, AnalysisConfig(3))[-1]
    assert [s.pivot_index for s in last.trend.swing_highs] == [9, 11]
    assert [s.pivot_index for s in last.trend.swing_lows] == [10, 12]
    assert last.trend.direction == TrendDirection.BEARISH
