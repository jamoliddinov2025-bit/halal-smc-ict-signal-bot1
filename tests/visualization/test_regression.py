from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from smcsignal.analysis import (
    StyleToken,
    VisualizationConfig,
    compose_drawing,
    load_visualization_config,
    render_svg,
    render_text,
)
from tests.visualization.helpers import model, streams

SRC = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"

# Modules whose decisions must never read visualization output.
DECISION_PACKAGES = (
    "structure.py",
    "swings.py",
    "trend.py",
    "liquidity",
    "displacement",
    "fvg",
    "order_blocks",
    "premium_discount",
    "mss",
    "breaker_blocks",
    "mitigation_blocks",
    "ote",
    "mtf",
    "halal_filter",
    "setup_quality",
    "signal_eligibility",
    "signal_engine",
    "outcome_tracking",
    "indicators",
    "setup_attribution",
    "performance",
    "review",
    "config.py",
    "models.py",
    "errors.py",
    "provenance.py",
)


def _sources() -> list[Path]:
    files: list[Path] = []
    for name in DECISION_PACKAGES:
        path = SRC / name
        if path.is_dir():
            files.extend(sorted(path.glob("*.py")))
        elif path.exists():
            files.append(path)
    return files


def test_drawing_composition_leaves_every_input_unchanged() -> None:
    primary, frames, indicators, outcomes = streams()
    saved = (
        deepcopy(primary),
        deepcopy(frames),
        deepcopy(indicators),
        deepcopy(outcomes),
    )
    drawing = compose_drawing(primary, signals=frames, indicators=indicators, outcomes=outcomes)
    assert primary == saved[0]
    assert frames == saved[1]
    assert indicators == saved[2]
    assert outcomes == saved[3]
    assert drawing.candles == tuple(frame.observation.evaluation for frame in primary)


def test_replayed_inputs_stay_identical_after_consumption() -> None:
    first = streams()
    second = streams()
    compose_drawing(first[0], signals=first[1], indicators=first[2], outcomes=first[3])
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert first[2] == second[2]
    assert first[3] == second[3]


def test_signal_statuses_are_unchanged_by_composition() -> None:
    _, frames, _, _ = streams()
    before = tuple(frame.status for frame in frames)
    model()
    assert tuple(frame.status for frame in frames) == before


def test_no_decision_module_imports_visualization_output() -> None:
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for token in (
            "analysis.visualization",
            "DrawingModel",
            "compose_drawing",
            "render_svg",
            "render_text",
        ):
            assert token not in text, f"{path.relative_to(SRC)} must not read drawings"


def test_visualization_module_imports_no_new_dependencies() -> None:
    from smcsignal.analysis.visualization import composer, svg, text

    for module in (composer, svg, text):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "requests",
            "urllib",
            "socket",
            "numpy",
            "pandas",
            "telegram",
            "png",
            "PIL",
        ):
            assert forbidden not in source


def test_renderers_emit_only_svg_and_plain_text() -> None:
    drawing = model()
    assert render_svg(drawing).startswith("<svg ")
    assert render_text(drawing).splitlines()[0].endswith("not advice")
    from smcsignal.analysis.visualization import svg as svg_module

    source = Path(svg_module.__file__).read_text(encoding="utf-8")
    assert "base64" not in source  # no embedded raster payloads


def test_analysis_package_exports_the_visualization_api() -> None:
    import smcsignal.analysis as analysis

    for name in (
        "DrawingModel",
        "EventMarker",
        "IndicatorOverlay",
        "LevelLine",
        "StyleToken",
        "TextAnnotation",
        "VisualizationConfig",
        "ZoneRect",
        "compose_drawing",
        "load_visualization_config",
        "render_signal_explanation",
        "render_svg",
        "render_text",
    ):
        assert getattr(analysis, name) is not None
    assert analysis.VisualizationConfig() == VisualizationConfig()
    assert (
        load_visualization_config(
            Path(__file__).resolve().parents[2] / "config" / "visualization.example.toml"
        )
        == VisualizationConfig()
    )


def test_example_configuration_documents_the_fact_only_role() -> None:
    text = (
        Path(__file__).resolve().parents[2] / "config" / "visualization.example.toml"
    ).read_text(encoding="utf-8")
    assert "never re-detects" in text
    assert "no PNG or raster" in text
    assert "telegram" not in text.lower()


def test_drawing_is_deterministic_across_repeated_composition() -> None:
    first = model()
    second = model()
    assert first == second
    assert first.drawing_id == second.drawing_id
    assert len({first, second}) == 1


def test_primitive_style_tokens_never_leak_colors() -> None:
    drawing = model()
    for primitive in drawing.primitives:
        assert isinstance(primitive.token, StyleToken)
    model_source = (SRC / "visualization" / "models.py").read_text(encoding="utf-8")
    for color in ("#", "rgb(", "0x"):
        assert color not in model_source  # colors live only in the SVG renderer
