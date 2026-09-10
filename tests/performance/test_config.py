from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.performance import (
    DEFAULT_MINIMUM_FINALIZED_FOR_RANKING,
    MAXIMUM_FINALIZED_FOR_RANKING,
    METHODOLOGY_VERSION,
    PerformanceConfig,
    load_performance_config,
)

PATH = Path(__file__).resolve().parents[2] / "config" / "performance.example.toml"


def test_defaults_are_frozen_and_explicit() -> None:
    config = PerformanceConfig()
    assert config.enabled is True
    assert config.minimum_finalized_for_ranking == 10
    assert DEFAULT_MINIMUM_FINALIZED_FOR_RANKING == 10
    assert MAXIMUM_FINALIZED_FOR_RANKING == 1000
    assert METHODOLOGY_VERSION == "performance-v1"
    with pytest.raises(FrozenInstanceError):
        config.minimum_finalized_for_ranking = 1  # type: ignore[misc]


def test_minimum_has_strict_bounds() -> None:
    assert PerformanceConfig(minimum_finalized_for_ranking=1).enabled is True
    assert (
        PerformanceConfig(minimum_finalized_for_ranking=1000).minimum_finalized_for_ranking == 1000
    )
    for invalid in (0, -1, 1001, 1.0, True, "10", None):
        with pytest.raises(AnalysisConfigurationError):
            PerformanceConfig(minimum_finalized_for_ranking=invalid)  # type: ignore[arg-type]


def test_enabled_must_be_true_and_boolean() -> None:
    with pytest.raises(AnalysisConfigurationError):
        PerformanceConfig(enabled=False)
    for invalid in (1, "true", None):
        with pytest.raises(AnalysisConfigurationError):
            PerformanceConfig(enabled=invalid)  # type: ignore[arg-type]


def write_table(tmp_path: Path, table: str) -> Path:
    path = tmp_path / "performance.toml"
    path.write_text(table, encoding="utf-8")
    return path


def test_loader_requires_the_exact_table(tmp_path: Path) -> None:
    for table in (
        "",
        "[other]\nenabled = true\n",
        "[performance]\n",
        "[performance]\nenabled = true\n",
        "[performance]\nminimum_finalized_for_ranking = 10\n",
        '[performance]\nenabled = true\nminimum_finalized_for_ranking = 10\nrank_by = "win_rate"\n',
        "[performance]\nenabled = true\nminimum_finalized_for_ranking = 10\nadvice = false\n",
        "[performance]\nenabled = true\nminimum_finalized_for_ranking = 10\noptimization = false\n",
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_performance_config(write_table(tmp_path, table))


def test_loader_rejects_invalid_values(tmp_path: Path) -> None:
    for table in (
        "[performance]\nenabled = false\nminimum_finalized_for_ranking = 10\n",
        "[performance]\nenabled = true\nminimum_finalized_for_ranking = 0\n",
        "[performance]\nenabled = true\nminimum_finalized_for_ranking = 1001\n",
        '[performance]\nenabled = true\nminimum_finalized_for_ranking = "10"\n',
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_performance_config(write_table(tmp_path, table))


def test_loader_accepts_the_exact_approved_table(tmp_path: Path) -> None:
    loaded = load_performance_config(
        write_table(tmp_path, "[performance]\nenabled = true\nminimum_finalized_for_ranking = 3\n")
    )
    assert loaded == PerformanceConfig(minimum_finalized_for_ranking=3)


def test_example_configuration_loads_and_matches_defaults() -> None:
    loaded = load_performance_config(PATH)
    assert loaded == PerformanceConfig()


def test_loader_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_performance_config(tmp_path / "missing.toml")
