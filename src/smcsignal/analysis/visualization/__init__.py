"""Phase 19e API: deterministic SVG and text rendering of published facts."""

from smcsignal.analysis.visualization.composer import compose_drawing, price_domain
from smcsignal.analysis.visualization.config import (
    METHODOLOGY_VERSION,
    VisualizationConfig,
    load_visualization_config,
)
from smcsignal.analysis.visualization.models import (
    RANKS,
    DrawingModel,
    EventMarker,
    IndicatorOverlay,
    LevelLine,
    Primitive,
    StyleToken,
    TextAnnotation,
    ZoneRect,
    canonical_order,
)
from smcsignal.analysis.visualization.svg import COLORS, render_svg
from smcsignal.analysis.visualization.text import (
    MARKS,
    render_signal_explanation,
    render_text,
)

__all__ = [
    "COLORS",
    "DrawingModel",
    "EventMarker",
    "IndicatorOverlay",
    "LevelLine",
    "MARKS",
    "METHODOLOGY_VERSION",
    "Primitive",
    "RANKS",
    "StyleToken",
    "TextAnnotation",
    "VisualizationConfig",
    "ZoneRect",
    "canonical_order",
    "compose_drawing",
    "load_visualization_config",
    "price_domain",
    "render_signal_explanation",
    "render_svg",
    "render_text",
]
