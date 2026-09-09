"""Regime classifier causality, determinism, and exact-metric tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.robustness import (
    RegimeAnalyzer,
    analyze_regimes,
    classify_regime,
    efficiency_ratio,
    volatility_ratio,
)
from smcsignal.analysis.robustness.models import MarketRegime, RegimeObservation
from smcsignal.data import OHLCV
from tests.robustness.helpers import bar_at, robustness

CLOSES_RISING = tuple(Decimal(str(value)) for value in (10, 11, 12, 13, 14, 15, 16))
# Alternating 10,12,10,12...: zero net move over any even window.
CLOSES_ALTERNATING = tuple(Decimal(str(10 + 2 * (index % 2))) for index in range(8))


def candles(closes: tuple) -> tuple[OHLCV, ...]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        bar_at(start + timedelta(minutes=15 * index), close) for index, close in enumerate(closes)
    )


def test_efficiency_ratio_is_one_for_a_purely_directional_window() -> None:
    assert efficiency_ratio(CLOSES_RISING, 3) == Decimal(1)


def test_efficiency_ratio_is_zero_for_a_flat_net_window() -> None:
    assert efficiency_ratio(CLOSES_ALTERNATING, 2) == Decimal(0)


def test_efficiency_ratio_is_an_exact_decimal_fraction() -> None:
    closes = tuple(Decimal(value) for value in (2, 5, 3, 7))
    # net |2->7| = 5, path 3+2+4 = 9, rounded to 50 fractional digits
    expected = Decimal("0.55555555555555555555555555555555555555555555555556")
    assert efficiency_ratio(closes, 3) == expected


def test_efficiency_ratio_requires_a_full_lookback() -> None:
    with pytest.raises(AnalysisInputError, match=r"full lookback"):
        efficiency_ratio(CLOSES_RISING[:3], 3)


def test_efficiency_ratio_rejects_nonpositive_lookback() -> None:
    with pytest.raises(AnalysisInputError, match=r"positive integer"):
        efficiency_ratio(CLOSES_RISING, 0)


def test_volatility_ratio_is_one_for_uniform_changes() -> None:
    assert volatility_ratio(CLOSES_RISING, 2, 4) == Decimal(1)


def test_volatility_ratio_detects_expansion_and_contraction() -> None:
    calm_then_wild = tuple(Decimal(value) for value in (10, 11, 12, 13, 13, 16, 13, 16, 20))
    # baseline mean |Δ| includes calm head, recent lookback is wild: ratio > 1.
    assert volatility_ratio(calm_then_wild, 4, 8) > 1
    wild_then_calm = tuple(Decimal(value) for value in (10, 20, 10, 20, 20, 21, 22, 23, 24))
    assert volatility_ratio(wild_then_calm, 4, 8) < 1


def test_volatility_ratio_is_none_for_an_entirely_flat_baseline() -> None:
    flat = tuple(Decimal(5) for _ in range(7))
    assert volatility_ratio(flat, 3, 6) is None


def test_volatility_ratio_is_an_exact_decimal_fraction() -> None:
    closes = tuple(Decimal(value) for value in (2, 3, 4, 5, 6, 6, 7))
    # recent (last 2 changes): mean |Δ| = (0+1)/2 = 0.5; baseline (all 6): 5/6
    assert volatility_ratio(closes, 2, 6) == Decimal(
        "0.60000000000000000000000000000000000000000000000000"
    )


def test_volatility_ratio_requires_a_full_baseline() -> None:
    with pytest.raises(AnalysisInputError, match=r"full baseline"):
        volatility_ratio(CLOSES_RISING[:4], 3, 6)


def test_volatility_ratio_rejects_baseline_below_lookback() -> None:
    with pytest.raises(AnalysisInputError, match=r"at least the lookback"):
        volatility_ratio(CLOSES_RISING, 4, 3)


def test_classify_regime_declares_none_without_metrics() -> None:
    config = robustness()
    assert classify_regime(None, None, config) is None


def test_classify_regime_trend_has_priority_over_volatility() -> None:
    config = robustness()
    assert classify_regime(Decimal("0.30"), Decimal("9"), config) is MarketRegime.TRENDING
    assert classify_regime(Decimal("1"), Decimal(0), config) is MarketRegime.TRENDING


def test_classify_regime_volatility_labels_and_precedence() -> None:
    config = robustness()
    assert classify_regime(Decimal("0.1"), Decimal("1.5"), config) is MarketRegime.HIGH_VOLATILITY
    assert classify_regime(Decimal("0.1"), Decimal("0.75"), config) is MarketRegime.LOW_VOLATILITY
    assert classify_regime(Decimal("0.1"), Decimal("1.0"), config) is MarketRegime.RANGING
    assert classify_regime(Decimal("0.1"), None, config) is MarketRegime.RANGING


def test_classify_regime_thresholds_are_inclusive() -> None:
    config = robustness(trend_threshold="0.5")
    assert classify_regime(Decimal("0.5"), None, config) is MarketRegime.TRENDING
    assert classify_regime(Decimal("0.4999"), None, config) is MarketRegime.RANGING


def test_classify_regime_requires_the_robustness_configuration() -> None:
    with pytest.raises(AnalysisConfigurationError):
        classify_regime(Decimal(1), Decimal(1), None)  # type: ignore[arg-type]


def test_analyzer_warms_up_before_labelling() -> None:
    analyzer = RegimeAnalyzer(robustness(regime_lookback_bars=2, regime_baseline_multiple=2))
    first = analyzer.update(candles(CLOSES_RISING)[0])
    assert first.regime is None and first.efficiency_ratio is None
    assert first.lookback_bars == 2 and first.baseline_bars == 4
    for candle in candles(CLOSES_RISING)[1:]:
        observation = analyzer.update(candle)
    assert observation.regime is MarketRegime.TRENDING
    assert observation.efficiency_ratio == Decimal(1)
    assert analyzer.processed_count == len(CLOSES_RISING)
    assert analyzer.latest is observation


def test_analyzer_is_deterministic_for_a_fixed_history() -> None:
    history = candles(CLOSES_RISING + CLOSES_ALTERNATING)
    first = analyze_regimes(history, robustness())
    second = analyze_regimes(history, robustness())
    assert first == second


def test_streaming_and_batch_regimes_are_identical() -> None:
    history = candles(CLOSES_RISING + CLOSES_ALTERNATING)
    config = robustness()
    analyzer = RegimeAnalyzer(config)
    streamed = tuple(analyzer.update(candle) for candle in history)
    assert streamed == analyze_regimes(history, config)


def test_regime_series_is_causal_prefix_stable() -> None:
    history = candles(CLOSES_RISING + CLOSES_ALTERNATING)
    config = robustness()
    full = analyze_regimes(history, config)
    prefix = analyze_regimes(history[:5], config)
    assert full[:5] == prefix


def test_regime_ignores_the_future_tail_completely() -> None:
    history = candles(CLOSES_RISING)
    config = robustness()
    before = analyze_regimes(history, config)
    extended = analyze_regimes(history + candles((Decimal(999), Decimal(5), Decimal(999))), config)
    assert extended[: len(history)] == before


def test_analyzer_rejects_non_ohlcv_input() -> None:
    analyzer = RegimeAnalyzer(robustness())
    with pytest.raises(AnalysisInputError):
        analyzer.update("2024-01-01")  # type: ignore[arg-type]


def test_analyze_regimes_rejects_non_iterable_input() -> None:
    with pytest.raises(AnalysisInputError):
        analyze_regimes(42, robustness())  # type: ignore[arg-type]


def test_regime_observation_rejects_inconsistent_facts() -> None:
    with pytest.raises(AnalysisInputError, match=r"regime label requires"):
        RegimeObservation(
            index=0,
            opened_at=datetime(2024, 1, 1, tzinfo=UTC),
            regime=MarketRegime.RANGING,
            efficiency_ratio=None,
            volatility_ratio=None,
            lookback_bars=3,
            baseline_bars=6,
        )
    with pytest.raises(AnalysisInputError, match=r"cannot exceed one"):
        RegimeObservation(
            index=0,
            opened_at=datetime(2024, 1, 1, tzinfo=UTC),
            regime=MarketRegime.TRENDING,
            efficiency_ratio=Decimal("1.5"),
            volatility_ratio=None,
            lookback_bars=3,
            baseline_bars=6,
        )


def test_regime_labels_never_reach_the_signal_pipeline() -> None:
    # The observation carries only descriptive facts: there is no score,
    # threshold, filter, veto, or publication field anywhere on it.
    observation = RegimeAnalyzer(robustness()).update(candles(CLOSES_RISING)[0])
    forbidden = {
        "score",
        "publish",
        "veto",
        "suppress",
        "filter",
        "gate",
        "weight",
        "threshold",
    }
    assert not forbidden & set(dir(observation))
