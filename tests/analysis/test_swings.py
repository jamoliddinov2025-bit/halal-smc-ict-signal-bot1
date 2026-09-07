"""Delayed, strict symmetric fractals and deterministic pivot metadata."""

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis import (
    AnalysisConfig,
    AnalysisInputError,
    SwingDetector,
    SwingKind,
    detect_swings,
)
from tests.analysis.helpers import bar, series


def test_five_bar_high_is_published_only_after_two_right_candles():
    candles = series([10, 12, 16, 13, 11])
    detector = SwingDetector(AnalysisConfig(fractal_length=5))
    for candle in candles[:4]:
        assert detector.update(candle) == ()
    confirmed = detector.update(candles[4])
    assert len(confirmed) == 1
    high = confirmed[0]
    assert high.kind == SwingKind.HIGH
    assert high.price == Decimal("17")
    assert high.pivot_index == 2
    assert high.confirmed_index == 4
    assert high.pivot_timestamp == candles[2].timestamp
    assert high.confirmed_timestamp == candles[4].timestamp


def test_five_bar_low_confirmation():
    candles = series([16, 13, 10, 12, 15])
    (low,) = detect_swings(candles, AnalysisConfig(5))
    assert (low.kind, low.pivot_index, low.confirmed_index, low.price) == (
        SwingKind.LOW,
        2,
        4,
        Decimal("9"),
    )


@pytest.mark.parametrize("length", [3, 5, 7, 9, 21, 101, 1001])
def test_configurable_length_changes_confirmation_delay(length):
    radius = (length - 1) // 2
    prices = [1000 - abs(index - radius) for index in range(length)]
    candles = series(prices)
    swings = detect_swings(candles, AnalysisConfig(length))
    assert [(s.kind, s.pivot_index, s.confirmed_index) for s in swings] == [
        (SwingKind.HIGH, radius, length - 1),
    ]


@pytest.mark.parametrize("side", [0, 1, 3, 4])
def test_high_tie_anywhere_in_window_disqualifies_high(side):
    candles = list(series([10, 12, 16, 13, 11]))
    candles[side] = replace(candles[side], high=candles[2].high)
    assert not any(s.kind == SwingKind.HIGH for s in detect_swings(candles, AnalysisConfig(5)))


@pytest.mark.parametrize("side", [0, 1, 3, 4])
def test_low_tie_anywhere_in_window_disqualifies_low(side):
    candles = list(series([16, 13, 10, 12, 15]))
    candles[side] = replace(candles[side], low=candles[2].low)
    assert not any(s.kind == SwingKind.LOW for s in detect_swings(candles, AnalysisConfig(5)))


def test_outside_candle_can_confirm_both_kinds_in_fixed_order():
    candles = (bar(0, 10, high=11, low=9), bar(1, 10, high=15, low=5), bar(2, 10, high=12, low=8))
    confirmed = detect_swings(candles, AnalysisConfig(3))
    assert [s.kind for s in confirmed] == [SwingKind.HIGH, SwingKind.LOW]
    assert all(s.pivot_index == 1 and s.confirmed_index == 2 for s in confirmed)


@pytest.mark.parametrize(
    "prices", [[], [10], [10, 12], [10, 11, 12, 13, 14], [14, 13, 12, 11, 10], [10] * 8]
)
def test_edges_monotonic_and_flat_data_do_not_fabricate_swings(prices):
    assert detect_swings(series(prices), AnalysisConfig(3)) == ()


def test_no_flushing_of_unconfirmed_tail_pivots():
    candles = series([10, 11, 12, 18, 14])
    assert detect_swings(candles, AnalysisConfig(5)) == ()  # pivot 3 still needs candle 5


def test_hand_computed_confirmations(golden_candles):
    result = detect_swings(golden_candles, AnalysisConfig(3))
    assert [(s.kind.value, s.pivot_index, s.confirmed_index) for s in result] == [
        ("high", 1, 2),
        ("low", 2, 3),
        ("high", 3, 4),
        ("low", 4, 5),
        ("high", 6, 7),
        ("low", 8, 9),
        ("high", 9, 10),
        ("low", 10, 11),
        ("high", 11, 12),
        ("low", 12, 13),
    ]


def test_high_and_low_use_wicks_not_open_or_close():
    candles = (bar(0, 10), bar(1, 10, high=20, low=2), bar(2, 10))
    result = detect_swings(candles, AnalysisConfig(3))
    assert [(s.kind, s.price) for s in result] == [
        (SwingKind.HIGH, Decimal(20)),
        (SwingKind.LOW, Decimal(2)),
    ]


def test_rejected_duplicate_does_not_advance_detector():
    detector = SwingDetector(AnalysisConfig(3))
    candles = series([10, 15, 12])
    detector.update(candles[0])
    with pytest.raises(AnalysisInputError, match="chronological"):
        detector.update(candles[0])
    assert detector.processed_count == 1
    assert detector.update(candles[1]) == ()
    assert detector.update(candles[2]) == detect_swings(candles, AnalysisConfig(3))


def test_gaps_are_counted_as_observations_not_synthetic_bars():
    candles = (bar(0, 10), bar(20, 15), bar(100, 12))
    (high,) = detect_swings(candles, AnalysisConfig(3))
    assert high.pivot_index == 1 and high.confirmed_index == 2
    assert high.confirmed_timestamp == candles[2].timestamp
