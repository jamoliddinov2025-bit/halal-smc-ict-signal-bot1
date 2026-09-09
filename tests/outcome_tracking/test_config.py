from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.analysis import OutcomeTrackingConfig, load_outcome_tracking_config
from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.outcome_tracking.config import (
    DEFAULT_HORIZON_BARS,
    MAX_HORIZON_BARS,
    METHODOLOGY_VERSION,
)

PATH = Path(__file__).resolve().parents[2] / "config" / "outcome-tracking.example.toml"


def test_defaults_are_frozen_and_explicit() -> None:
    config = OutcomeTrackingConfig()
    assert config.enabled is True
    assert config.horizon_bars == DEFAULT_HORIZON_BARS == 10
    assert METHODOLOGY_VERSION == "outcome-tracking-v1"
    with pytest.raises(FrozenInstanceError):
        config.horizon_bars = 5  # type: ignore[misc]


def test_horizon_bounds_are_strict() -> None:
    assert OutcomeTrackingConfig(horizon_bars=1).horizon_bars == 1
    assert OutcomeTrackingConfig(horizon_bars=MAX_HORIZON_BARS).horizon_bars == MAX_HORIZON_BARS
    for invalid in (0, -1, MAX_HORIZON_BARS + 1):
        with pytest.raises(AnalysisConfigurationError):
            OutcomeTrackingConfig(horizon_bars=invalid)
    for invalid in (1.0, True, "10", None):
        with pytest.raises(AnalysisConfigurationError):
            OutcomeTrackingConfig(horizon_bars=invalid)  # type: ignore[arg-type]


def test_enabled_must_be_true_and_boolean() -> None:
    with pytest.raises(AnalysisConfigurationError):
        OutcomeTrackingConfig(enabled=False)
    for invalid in (1, "true", None):
        with pytest.raises(AnalysisConfigurationError):
            OutcomeTrackingConfig(enabled=invalid)  # type: ignore[arg-type]


def write_table(tmp_path: Path, table: str) -> Path:
    path = tmp_path / "outcome.toml"
    path.write_text(table, encoding="utf-8")
    return path


def test_loader_requires_the_exact_table(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_outcome_tracking_config(write_table(tmp_path, ""))
    with pytest.raises(AnalysisConfigurationError):
        load_outcome_tracking_config(write_table(tmp_path, "[other]\nenabled = true\n"))


@pytest.mark.parametrize(
    "table",
    [
        "[outcome_tracking]\nenabled = true\n",
        "[outcome_tracking]\nhorizon_bars = 10\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = 10\nextra = 1\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = 10\nentry = 100\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = 10\nstop = 90\n",
        '[outcome_tracking]\nenabled = true\nhorizon_bars = 10\nfees = "0.1"\n',
        "[outcome_tracking]\nenabled = true\nhorizon_bars = 10\ntake_profit = 120\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = 10\nposition_size = 1\n",
        '[outcome_tracking]\nenabled = true\nhorizon_bars = 10\nslippage = "0"\n',
    ],
)
def test_loader_rejects_missing_and_unknown_keys(tmp_path: Path, table: str) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_outcome_tracking_config(write_table(tmp_path, table))


@pytest.mark.parametrize(
    "table",
    [
        "[outcome_tracking]\nenabled = false\nhorizon_bars = 10\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = 0\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = -3\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = 10.5\n",
        "[outcome_tracking]\nenabled = true\nhorizon_bars = true\n",
        '[outcome_tracking]\nenabled = "true"\nhorizon_bars = 10\n',
    ],
)
def test_loader_rejects_invalid_values(tmp_path: Path, table: str) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_outcome_tracking_config(write_table(tmp_path, table))


def test_loader_accepts_the_exact_approved_table(tmp_path: Path) -> None:
    loaded = load_outcome_tracking_config(
        write_table(tmp_path, "[outcome_tracking]\nenabled = true\nhorizon_bars = 4\n")
    )
    assert loaded == OutcomeTrackingConfig(horizon_bars=4)
    assert loaded is not None and type(loaded.horizon_bars) is int


def test_example_configuration_loads_and_matches_defaults() -> None:
    loaded = load_outcome_tracking_config(PATH)
    assert loaded == OutcomeTrackingConfig()


def test_loader_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_outcome_tracking_config(tmp_path / "missing.toml")
