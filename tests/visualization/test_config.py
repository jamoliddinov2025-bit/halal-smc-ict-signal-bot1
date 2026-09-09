from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.visualization import (
    METHODOLOGY_VERSION,
    VisualizationConfig,
    load_visualization_config,
)

PATH = Path(__file__).resolve().parents[2] / "config" / "visualization.example.toml"


def test_defaults_are_frozen_and_explicit() -> None:
    config = VisualizationConfig()
    assert config.enabled is True
    assert (config.svg_width, config.svg_height, config.text_rows) == (800, 400, 24)
    assert METHODOLOGY_VERSION == "visualization-v1"
    with pytest.raises(FrozenInstanceError):
        config.text_rows = 10  # type: ignore[misc]


@pytest.mark.parametrize("field", ["svg_width", "svg_height"])
def test_canvas_has_strict_bounds(field: str) -> None:
    assert getattr(VisualizationConfig(**{field: 100}), field) == 100
    assert getattr(VisualizationConfig(**{field: 10_000}), field) == 10_000
    for invalid in (99, 10_001, -1, 800.0, True, "800", None):
        with pytest.raises(AnalysisConfigurationError):
            VisualizationConfig(**{field: invalid})  # type: ignore[arg-type]


def test_text_rows_have_strict_bounds() -> None:
    assert VisualizationConfig(text_rows=5).text_rows == 5
    assert VisualizationConfig(text_rows=200).text_rows == 200
    for invalid in (4, 201, 0, 24.0, True, "24", None):
        with pytest.raises(AnalysisConfigurationError):
            VisualizationConfig(text_rows=invalid)  # type: ignore[arg-type]


def test_enabled_must_be_true_and_boolean() -> None:
    with pytest.raises(AnalysisConfigurationError):
        VisualizationConfig(enabled=False)
    for invalid in (1, "true", None):
        with pytest.raises(AnalysisConfigurationError):
            VisualizationConfig(enabled=invalid)  # type: ignore[arg-type]


def write_table(tmp_path: Path, table: str) -> Path:
    path = tmp_path / "visualization.toml"
    path.write_text(table, encoding="utf-8")
    return path


def test_loader_requires_the_exact_table(tmp_path: Path) -> None:
    for table in (
        "",
        "[other]\nenabled = true\n",
        "[visualization]\n",
        "[visualization]\nsvg_width = 800\n",
        "[visualization]\nenabled = true\n",
        "[visualization]\nenabled = true\nsvg_width = 800\nsvg_height = 400\n",
        "[visualization]\nenabled = true\nsvg_width = 800\nsvg_height = 400\n"
        'text_rows = 24\ntheme = "dark"\n',
        "[visualization]\nenabled = true\nsvg_width = 800\nsvg_height = 400\n"
        "text_rows = 24\npng = false\n",
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_visualization_config(write_table(tmp_path, table))


def test_loader_rejects_invalid_values(tmp_path: Path) -> None:
    for table in (
        "[visualization]\nenabled = false\nsvg_width = 800\nsvg_height = 400\ntext_rows = 24\n",
        "[visualization]\nenabled = true\nsvg_width = 99\nsvg_height = 400\ntext_rows = 24\n",
        "[visualization]\nenabled = true\nsvg_width = 800\nsvg_height = 10001\ntext_rows = 24\n",
        "[visualization]\nenabled = true\nsvg_width = 800\nsvg_height = 400\ntext_rows = 4\n",
        '[visualization]\nenabled = true\nsvg_width = "800"\nsvg_height = 400\ntext_rows = 24\n',
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_visualization_config(write_table(tmp_path, table))


def test_loader_accepts_the_exact_approved_table(tmp_path: Path) -> None:
    loaded = load_visualization_config(
        write_table(
            tmp_path,
            "[visualization]\nenabled = true\nsvg_width = 640\nsvg_height = 320\ntext_rows = 12\n",
        )
    )
    assert loaded == VisualizationConfig(svg_width=640, svg_height=320, text_rows=12)


def test_example_configuration_loads_and_matches_defaults() -> None:
    loaded = load_visualization_config(PATH)
    assert loaded == VisualizationConfig()


def test_loader_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_visualization_config(tmp_path / "missing.toml")
