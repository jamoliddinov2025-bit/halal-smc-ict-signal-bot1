from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.setup_attribution import (
    METHODOLOGY_VERSION,
    SetupAttributionConfig,
    load_setup_attribution_config,
)

PATH = Path(__file__).resolve().parents[2] / "config" / "setup-attribution.example.toml"


def test_defaults_are_frozen_and_enabled() -> None:
    config = SetupAttributionConfig()
    assert config.enabled is True
    assert METHODOLOGY_VERSION == "setup-attribution-v1"
    with pytest.raises(FrozenInstanceError):
        config.enabled = False  # type: ignore[misc]


def test_enabled_must_be_true_and_boolean() -> None:
    with pytest.raises(AnalysisConfigurationError):
        SetupAttributionConfig(enabled=False)
    for invalid in (1, "true", None):
        with pytest.raises(AnalysisConfigurationError):
            SetupAttributionConfig(enabled=invalid)  # type: ignore[arg-type]


def write_table(tmp_path: Path, table: str) -> Path:
    path = tmp_path / "setup_attribution.toml"
    path.write_text(table, encoding="utf-8")
    return path


def test_loader_requires_the_exact_table(tmp_path: Path) -> None:
    for table in (
        "",
        "[other]\nenabled = true\n",
        "[setup_attribution]\n",
        "[setup_attribution]\nenabled = true\nlabels = []\n",
        "[setup_attribution]\nenabled = true\nthreshold = 70\n",
        "[setup_attribution]\nenabled = true\noutcome_dependence = false\n",
        "[setup_attribution]\nenabled = true\nscore_threshold = 25\n",
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_setup_attribution_config(write_table(tmp_path, table))


def test_loader_rejects_invalid_values(tmp_path: Path) -> None:
    for table in (
        "[setup_attribution]\nenabled = false\n",
        "[setup_attribution]\nenabled = 1\n",
        '[setup_attribution]\nenabled = "true"\n',
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_setup_attribution_config(write_table(tmp_path, table))


def test_loader_accepts_the_exact_approved_table(tmp_path: Path) -> None:
    loaded = load_setup_attribution_config(
        write_table(tmp_path, "[setup_attribution]\nenabled = true\n")
    )
    assert loaded == SetupAttributionConfig()


def test_example_configuration_loads_and_matches_defaults() -> None:
    loaded = load_setup_attribution_config(PATH)
    assert loaded == SetupAttributionConfig()


def test_loader_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_setup_attribution_config(tmp_path / "missing.toml")
