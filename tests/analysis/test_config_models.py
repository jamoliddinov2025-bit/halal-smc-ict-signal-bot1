"""Typed immutable outputs, configuration validation, and causal metadata guards."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from smcsignal.analysis import (
    AnalysisConfig,
    AnalysisConfigurationError,
    AnalysisInputError,
    AnalysisSnapshot,
    MarketStructureAnalyzer,
    StructureEventKind,
    SwingDetector,
    SwingKind,
    TrendDirection,
    analyze,
    classify_trend,
    load_analysis_config,
)
from tests.analysis.helpers import BASE, STEP, swing


@pytest.mark.parametrize("length", [3, 5, 7, 99, 1001])
def test_valid_total_window_lengths(length):
    config = AnalysisConfig(length)
    assert config.fractal_length == length
    assert config.confirmation_delay == (length - 1) // 2


@pytest.mark.parametrize(
    "length", [0, 1, 2, 4, -1, 1002, 1003, True, False, 5.0, "5", None, {}, Decimal(5), 10**50 + 1]
)
def test_invalid_fractal_lengths_are_rejected(length):
    with pytest.raises(AnalysisConfigurationError, match="fractal_length"):
        AnalysisConfig(length)


def test_default_is_five_candle_window_with_two_candle_delay():
    assert AnalysisConfig().fractal_length == 5
    assert AnalysisConfig().confirmation_delay == 2


def test_configuration_cannot_change_midstream():
    config = AnalysisConfig(3)
    with pytest.raises(FrozenInstanceError):
        config.fractal_length = 5
    for component in (SwingDetector(config), MarketStructureAnalyzer(config)):
        with pytest.raises(AttributeError):
            component.config = AnalysisConfig(5)


@pytest.mark.parametrize("config", [3, {}, "5", False])
def test_analyzers_require_validated_configuration(config):
    with pytest.raises(AnalysisConfigurationError):
        SwingDetector(config)
    with pytest.raises(AnalysisConfigurationError):
        MarketStructureAnalyzer(config)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("example.toml", 5), ("binance-public.example.toml", 5), ("analysis.example.toml", 3)],
)
def test_example_configurations_load_without_network(filename, expected):
    path = Path(__file__).resolve().parents[2] / "config" / filename
    assert load_analysis_config(path).fractal_length == expected


@pytest.mark.parametrize(
    "document",
    [
        "",
        "[analysis",
        "[analysis]",
        'analysis="5"',
        "[analysis]\nfractal_length=4",
        "[analysis]\nfractal_length=true",
        "[analysis]\nfractal_length=5\nunknown=1",
    ],
)
def test_malformed_or_unknown_analysis_settings_fail(tmp_path, document):
    path = tmp_path / "analysis.toml"
    path.write_text(document)
    with pytest.raises(AnalysisConfigurationError):
        load_analysis_config(path)


def test_missing_configuration_file_is_actionable(tmp_path):
    with pytest.raises(AnalysisConfigurationError, match="cannot load"):
        load_analysis_config(tmp_path / "missing.toml")


def test_loader_reads_only_the_analysis_table(tmp_path):
    path = tmp_path / "analysis.toml"
    path.write_text("[project]\nphase=3\n[analysis]\nfractal_length=7\n")
    assert load_analysis_config(path) == AnalysisConfig(7)


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": "high"},
        {"pivot_index": True},
        {"pivot_index": -1},
        {"confirmed_index": 1},
        {"confirmed_timestamp": BASE + STEP},
        {"pivot_timestamp": datetime(2024, 1, 1)},
        {"price": 5.0},
        {"price": Decimal("NaN")},
        {"price": Decimal("Infinity")},
        {"price": Decimal(0)},
    ],
)
def test_swing_model_rejects_invalid_or_noncausal_metadata(updates):
    with pytest.raises(AnalysisInputError):
        replace(swing(SwingKind.HIGH, 100, 1), **updates)


def test_confirmed_swing_is_immutable():
    high = swing(SwingKind.HIGH, 100, 1)
    with pytest.raises(FrozenInstanceError):
        high.price = Decimal(200)


@pytest.mark.parametrize(
    "updates",
    [
        {"candle_index": -1},
        {"timestamp": datetime(2024, 1, 1)},
        {"direction": "bullish"},
        {"swing_highs": []},
    ],
)
def test_trend_state_model_validation(updates):
    result = classify_trend(candle_index=10, timestamp=BASE + 10 * STEP)
    with pytest.raises(AnalysisInputError):
        replace(result, **updates)


def test_unsorted_duplicate_and_excess_swing_evidence_is_rejected():
    first, second = swing(SwingKind.HIGH, 100, 1), swing(SwingKind.HIGH, 110, 5)
    for evidence in (
        (second, first),
        (first, first),
        (first, second, swing(SwingKind.HIGH, 120, 7)),
    ):
        with pytest.raises(AnalysisInputError):
            classify_trend(candle_index=10, timestamp=BASE + 10 * STEP, swing_highs=evidence)


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": "BOS"},
        {"direction": TrendDirection.RANGING},
        {"trend_before": TrendDirection.RANGING},
        {"kind": StructureEventKind.CHOCH},
        {"previous_close": Decimal(30)},
        {"close": Decimal(19)},
        {"close": Decimal("NaN")},
        {"candle_index": 4},
        {"timestamp": BASE + 4 * STEP},
        {"level": None},
    ],
)
def test_structure_event_rejects_unavailable_levels_or_invalid_crossings(golden_candles, updates):
    event = analyze(golden_candles, AnalysisConfig(3))[6].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event, **updates)


def test_snapshot_event_swing_and_trend_collections_cannot_repaint(golden_candles):
    results = analyze(golden_candles, AnalysisConfig(3))
    snapshot = results[6]
    with pytest.raises(FrozenInstanceError):
        snapshot.candle_index = 99
    with pytest.raises(FrozenInstanceError):
        snapshot.trend.direction = TrendDirection.BEARISH
    with pytest.raises(FrozenInstanceError):
        snapshot.events[0].close = Decimal(1)
    with pytest.raises(AnalysisInputError):
        replace(snapshot, events=list(snapshot.events))
    with pytest.raises(AnalysisInputError):
        replace(snapshot, confirmed_swings=[])


def test_snapshot_cannot_backdate_a_confirmation_to_its_pivot(golden_candles):
    results = analyze(golden_candles, AnalysisConfig(3))
    high = results[2].confirmed_swings[0]
    with pytest.raises(AnalysisInputError, match="confirmation"):
        AnalysisSnapshot(
            candle_index=1,
            timestamp=golden_candles[1].timestamp,
            confirmed_swings=(high,),
            trend=results[1].trend,
            events=(),
        )


def test_snapshot_rejects_wrong_candle_trend_event_or_duplicate_swings(golden_candles):
    results = analyze(golden_candles, AnalysisConfig(3))
    with pytest.raises(AnalysisInputError, match="trend"):
        replace(results[6], trend=results[5].trend)
    with pytest.raises(AnalysisInputError, match="events"):
        replace(results[7], events=results[6].events)
    with pytest.raises(AnalysisInputError, match="duplicate swing"):
        replace(results[2], confirmed_swings=results[2].confirmed_swings * 2)
