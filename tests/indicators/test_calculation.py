from __future__ import annotations

from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.indicators import (
    EMACalculator,
    RSICalculator,
    VolumeAverageCalculator,
    average,
    ema_seed,
    ema_update,
    rsi_from_averages,
    volume_ratio,
    wilder_update,
)
from tests.indicators.helpers import ratio_like


def test_average_is_descriptive_and_reference_computed() -> None:
    values = (Decimal(1), Decimal(2), Decimal(3))
    assert average(values) == Decimal(2)
    assert average((Decimal(1), Decimal(1), Decimal(1))) == Decimal(1)
    third = average((Decimal(1), Decimal(1), Decimal(0)))
    assert third == ratio_like(Decimal(2), Decimal(3))
    with pytest.raises(AnalysisInputError):
        average(())


def test_ema_seed_and_step_are_hand_computable() -> None:
    assert ema_seed((Decimal(20), Decimal(21), Decimal(22))) == Decimal(21)
    # multiplier 2/(3+1) = 0.5 exactly: 21 + (23-21)*0.5 = 22
    assert ema_update(Decimal(21), Decimal(23), 3) == Decimal(22)
    # multiplier 2/(5+1): 50-digit reference
    expected = Decimal(22) + (Decimal(25) - Decimal(22)) * ratio_like(Decimal(2), Decimal(6))
    assert ema_update(Decimal(22), Decimal(25), 5) == expected


def test_wilder_update_matches_reference() -> None:
    expected = ratio_like(Decimal("1.5") * 2 + Decimal(1), Decimal(3))
    assert wilder_update(Decimal("1.5"), Decimal(1), 3) == expected
    assert wilder_update(Decimal(0), Decimal(2), 3) == ratio_like(Decimal(2), Decimal(3))


def test_rsi_edge_conventions_are_exact() -> None:
    assert rsi_from_averages(Decimal(0), Decimal(0)) == Decimal("50")
    assert rsi_from_averages(Decimal("0.5"), Decimal(0)) == Decimal(100)
    assert rsi_from_averages(Decimal(0), Decimal("0.5")) == Decimal(0)
    mixed = rsi_from_averages(Decimal(2), Decimal(1))
    assert mixed == ratio_like(Decimal(200), Decimal(3))
    with pytest.raises(AnalysisInputError):
        rsi_from_averages(Decimal(-1), Decimal(1))


def test_volume_ratio_is_descriptive() -> None:
    assert volume_ratio(Decimal(2), Decimal(2)) == Decimal(1)
    assert volume_ratio(Decimal(3), Decimal(2)) == Decimal("1.5")
    with pytest.raises(AnalysisInputError):
        volume_ratio(Decimal(3), Decimal(0))


def test_ema_calculator_warm_up_and_exact_progression() -> None:
    ema = EMACalculator(3)
    state, value = ema.update(Decimal(20))
    assert value is None
    state, value = state.update(Decimal(21))
    assert value is None
    state, value = state.update(Decimal(22))
    assert value == Decimal(21)
    progression = [value]
    for close in range(23, 27):
        state, value = state.update(Decimal(close))
        progression.append(value)
    assert progression == [Decimal(21), Decimal(22), Decimal(23), Decimal(24), Decimal(25)]


def test_ema_calculator_is_immutable_per_step() -> None:
    ema = EMACalculator(2)
    state, value = ema.update(Decimal(10))
    assert value is None and ema.value is None
    state, value = state.update(Decimal(12))
    assert value == Decimal(11)
    assert ema.value is None  # the original state is untouched
    state2, _ = state.update(Decimal(14))
    assert state2.value == Decimal(13) and state.value == Decimal(11)


def test_rsi_calculator_rising_flat_falling_and_mixed() -> None:
    rising = RSICalculator(3)
    values = []
    for close in (20, 21, 22, 23, 24):
        rising, value = rising.update(Decimal(close))
        values.append(value)
    assert values == [None, None, None, Decimal(100), Decimal(100)]

    flat = RSICalculator(3)
    values = []
    for _ in range(5):
        flat, value = flat.update(Decimal(30))
        values.append(value)
    assert values == [None, None, None, Decimal("50"), Decimal("50")]

    falling = RSICalculator(3)
    values = []
    for close in (30, 29, 28, 27):
        falling, value = falling.update(Decimal(close))
        values.append(value)
    assert values == [None, None, None, Decimal(0)]

    mixed = RSICalculator(3)
    values = []
    for close in (10, 12, 11, 13, 12):
        mixed, value = mixed.update(Decimal(close))
        values.append(value)
    assert values[:3] == [None, None, None]
    from tests.indicators.helpers import at50

    average_gain = ratio_like(Decimal(4), Decimal(3))
    average_loss = ratio_like(Decimal(1), Decimal(3))
    assert values[3] == at50(lambda: Decimal(100) * average_gain / (average_gain + average_loss))
    wilder_gain = at50(lambda: (average_gain * 2 + 0) / Decimal(3))
    wilder_loss = at50(lambda: (average_loss * 2 + 1) / Decimal(3))
    assert values[4] == at50(lambda: Decimal(100) * wilder_gain / (wilder_gain + wilder_loss))


def test_volume_calculator_warm_up_and_rolling_window() -> None:
    calc = VolumeAverageCalculator(3)
    results = []
    for volume in (1, 2, 3, 4, 5, 6):
        calc, average_value, ratio = calc.update(Decimal(volume))
        results.append((average_value, ratio))
    assert results == [
        (None, None),
        (None, None),
        (Decimal(2), Decimal("1.5")),
        (Decimal(3), ratio_like(Decimal(4), Decimal(3))),
        (Decimal(4), Decimal("1.25")),
        (Decimal(5), ratio_like(Decimal(6), Decimal(5))),
    ]
    with pytest.raises(AnalysisInputError):
        calc.update(Decimal("NaN"))
