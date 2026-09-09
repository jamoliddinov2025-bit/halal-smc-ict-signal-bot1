"""Robustness configuration and TOML loader tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.robustness import (
    RobustnessConfig,
    load_robustness_config,
)

FULL_TOML = """
[robustness]
enabled = true
development_bars = 12
validation_bars = 14
step_bars = 14
regime_lookback_bars = 3
regime_baseline_multiple = 2
trend_threshold = "0.30"
high_volatility_threshold = "1.50"
low_volatility_threshold = "0.75"
minimum_finalized_for_stability = 1
minimum_windows_for_stability = 2
stability_win_rate_floor = "0.50"
maximum_win_rate_spread = "0.50"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "robustness.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_construct_and_declare_the_documented_values() -> None:
    config = RobustnessConfig()
    assert config.enabled is True
    assert (config.development_bars, config.validation_bars, config.step_bars) == (50, 25, 25)
    assert config.regime_baseline_bars == 80


def test_step_must_cover_the_validation_period() -> None:
    with pytest.raises(AnalysisConfigurationError, match=r"validation segments never overlap"):
        RobustnessConfig(validation_bars=10, step_bars=9)
    RobustnessConfig(validation_bars=10, step_bars=10)  # exactly disjoint is allowed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("development_bars", 0),
        ("validation_bars", 0),
        ("step_bars", 0),
        ("regime_lookback_bars", 0),
        ("regime_baseline_multiple", 0),
        ("minimum_finalized_for_stability", 0),
        ("minimum_windows_for_stability", 0),
    ],
)
def test_integer_fields_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(AnalysisConfigurationError, match=field):
        RobustnessConfig(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trend_threshold", Decimal("0")),
        ("trend_threshold", Decimal("-0.1")),
        ("trend_threshold", Decimal("1.1")),
        ("high_volatility_threshold", Decimal("0")),
        ("low_volatility_threshold", Decimal("-1")),
        ("stability_win_rate_floor", Decimal("0")),
        ("stability_win_rate_floor", Decimal("1.1")),
        ("maximum_win_rate_spread", Decimal("1.1")),
    ],
)
def test_decimal_thresholds_must_be_in_range(field: str, value: Decimal) -> None:
    with pytest.raises(AnalysisConfigurationError, match=field):
        RobustnessConfig(**{field: value})


def test_low_volatility_threshold_must_stay_below_high() -> None:
    with pytest.raises(AnalysisConfigurationError, match=r"below high_volatility_threshold"):
        RobustnessConfig(low_volatility_threshold=Decimal("1.50"))


def test_enabled_must_be_true() -> None:
    for value in (False, 1, "true", None):
        with pytest.raises(AnalysisConfigurationError, match=r"enabled must be true"):
            RobustnessConfig(enabled=value)  # type: ignore[arg-type]


def test_load_robustness_config_reads_the_exact_table(tmp_path: Path) -> None:
    config = load_robustness_config(write(tmp_path, FULL_TOML))
    assert config.development_bars == 12
    assert config.trend_threshold == Decimal("0.30")
    assert config.regime_baseline_bars == 6


def test_load_robustness_config_rejects_missing_table(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError, match=r"must contain exactly"):
        load_robustness_config(write(tmp_path, "[other]\nenabled = true\n"))


def test_load_robustness_config_rejects_unknown_keys(tmp_path: Path) -> None:
    text = FULL_TOML + "optimize = true\n"
    with pytest.raises(AnalysisConfigurationError, match=r"must contain exactly"):
        load_robustness_config(write(tmp_path, text))


def test_load_robustness_config_rejects_disabled(tmp_path: Path) -> None:
    text = FULL_TOML.replace("enabled = true", "enabled = false")
    with pytest.raises(AnalysisConfigurationError, match=r"enabled must be true"):
        load_robustness_config(write(tmp_path, text))


def test_load_robustness_config_rejects_overlapping_validation(tmp_path: Path) -> None:
    text = FULL_TOML.replace("step_bars = 14", "step_bars = 13")
    with pytest.raises(AnalysisConfigurationError, match=r"never overlap"):
        load_robustness_config(write(tmp_path, text))


def test_load_robustness_config_parses_decimal_strings(tmp_path: Path) -> None:
    config = load_robustness_config(write(tmp_path, FULL_TOML))
    assert isinstance(config.trend_threshold, Decimal)
    assert isinstance(config.maximum_win_rate_spread, Decimal)


def test_load_robustness_config_rejects_malformed_decimals(tmp_path: Path) -> None:
    text = FULL_TOML.replace('trend_threshold = "0.30"', 'trend_threshold = "abc"')
    with pytest.raises(AnalysisConfigurationError, match=r"decimal string"):
        load_robustness_config(write(tmp_path, text))


def test_load_robustness_config_rejects_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[robustness\nenabled =", encoding="utf-8")
    with pytest.raises(AnalysisConfigurationError, match=r"cannot load robustness configuration"):
        load_robustness_config(path)


def test_load_robustness_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError, match=r"cannot load robustness configuration"):
        load_robustness_config(tmp_path / "absent.toml")
