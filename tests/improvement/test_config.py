"""Strict Phase 23A [improvement] configuration invariants."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.improvement import (
    DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON,
    METHODOLOGY_VERSION,
    ImprovementConfig,
    load_improvement_config,
)

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"

GOOD = """\
[improvement]
enabled = true
minimum_finalized_for_comparison = 30
human_approval_required = true
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "improvement.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_methodology_and_frozen_defaults_are_safe() -> None:
    assert METHODOLOGY_VERSION == "improvement-v1"
    defaults = ImprovementConfig()
    assert defaults.enabled is True
    assert defaults.minimum_finalized_for_comparison == 30
    assert defaults.human_approval_required is True


def test_module_default_constant_agrees() -> None:
    assert DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON == 30
    assert (
        DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON
        == ImprovementConfig().minimum_finalized_for_comparison
    )


def test_load_exact_table_round_trips(tmp_path: Path) -> None:
    assert load_improvement_config(_write(tmp_path, GOOD)) == ImprovementConfig()


def test_shipped_example_config_loads() -> None:
    assert load_improvement_config(CONFIG_ROOT / "improvement.example.toml") == ImprovementConfig()


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_improvement_config(_write(tmp_path, GOOD + "extra = 1\n"))


def test_missing_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_improvement_config(_write(tmp_path, "[improvement]\nenabled = true\n"))


def test_enabled_false_is_rejected(tmp_path: Path) -> None:
    bad = GOOD.replace("enabled = true", "enabled = false")
    with pytest.raises(AnalysisConfigurationError):
        load_improvement_config(_write(tmp_path, bad))


def test_human_approval_false_is_rejected(tmp_path: Path) -> None:
    bad = GOOD.replace("human_approval_required = true", "human_approval_required = false")
    with pytest.raises(AnalysisConfigurationError):
        load_improvement_config(_write(tmp_path, bad))


def test_minimum_zero_is_rejected(tmp_path: Path) -> None:
    bad = GOOD.replace(
        "minimum_finalized_for_comparison = 30", "minimum_finalized_for_comparison = 0"
    )
    with pytest.raises(AnalysisConfigurationError):
        load_improvement_config(_write(tmp_path, bad))


def test_minimum_non_scalar_is_rejected(tmp_path: Path) -> None:
    bad = GOOD.replace(
        "minimum_finalized_for_comparison = 30",
        "minimum_finalized_for_comparison = [1, 2]",
    )
    with pytest.raises(AnalysisConfigurationError):
        load_improvement_config(_write(tmp_path, bad))


def test_missing_table_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_improvement_config(_write(tmp_path, "[other]\nx = 1\n"))


def test_config_is_frozen() -> None:
    config = ImprovementConfig()
    with pytest.raises(FrozenInstanceError):
        config.enabled = False  # type: ignore[misc]
