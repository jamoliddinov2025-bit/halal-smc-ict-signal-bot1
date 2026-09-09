from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.review import (
    DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON,
    MAXIMUM_FINALIZED_FOR_COMPARISON,
    METHODOLOGY_VERSION,
    ReviewConfig,
    load_review_config,
)

PATH = Path(__file__).resolve().parents[2] / "config" / "review.example.toml"


def test_defaults_are_frozen_and_explicit() -> None:
    config = ReviewConfig()
    assert config.enabled is True
    assert config.minimum_finalized_for_comparison == 10
    assert DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON == 10
    assert MAXIMUM_FINALIZED_FOR_COMPARISON == 1000
    assert METHODOLOGY_VERSION == "monthly-review-v1"
    with pytest.raises(FrozenInstanceError):
        config.minimum_finalized_for_comparison = 1  # type: ignore[misc]


def test_minimum_has_strict_bounds() -> None:
    assert ReviewConfig(minimum_finalized_for_comparison=1).enabled is True
    assert (
        ReviewConfig(minimum_finalized_for_comparison=1000).minimum_finalized_for_comparison == 1000
    )
    for invalid in (0, -1, 1001, 1.5, True, "10", None):
        with pytest.raises(AnalysisConfigurationError):
            ReviewConfig(minimum_finalized_for_comparison=invalid)  # type: ignore[arg-type]


def test_enabled_must_be_true_and_boolean() -> None:
    with pytest.raises(AnalysisConfigurationError):
        ReviewConfig(enabled=False)
    for invalid in (1, "true", None):
        with pytest.raises(AnalysisConfigurationError):
            ReviewConfig(enabled=invalid)  # type: ignore[arg-type]


def write_table(tmp_path: Path, table: str) -> Path:
    path = tmp_path / "review.toml"
    path.write_text(table, encoding="utf-8")
    return path


def test_loader_requires_the_exact_table(tmp_path: Path) -> None:
    for table in (
        "",
        "[other]\nenabled = true\n",
        "[review]\n",
        "[review]\nenabled = true\n",
        "[review]\nminimum_finalized_for_comparison = 10\n",
        "[review]\nenabled = true\nminimum_finalized_for_comparison = 10\nadvice = false\n",
        "[review]\nenabled = true\nminimum_finalized_for_comparison = 10\nforecast = false\n",
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_review_config(write_table(tmp_path, table))


def test_loader_rejects_invalid_values(tmp_path: Path) -> None:
    for table in (
        "[review]\nenabled = false\nminimum_finalized_for_comparison = 10\n",
        "[review]\nenabled = true\nminimum_finalized_for_comparison = 0\n",
        "[review]\nenabled = true\nminimum_finalized_for_comparison = 1001\n",
        '[review]\nenabled = true\nminimum_finalized_for_comparison = "10"\n',
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_review_config(write_table(tmp_path, table))


def test_loader_accepts_the_exact_approved_table(tmp_path: Path) -> None:
    loaded = load_review_config(
        write_table(tmp_path, "[review]\nenabled = true\nminimum_finalized_for_comparison = 4\n")
    )
    assert loaded == ReviewConfig(minimum_finalized_for_comparison=4)


def test_example_configuration_loads_and_matches_defaults() -> None:
    loaded = load_review_config(PATH)
    assert loaded == ReviewConfig()


def test_loader_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_review_config(tmp_path / "missing.toml")
