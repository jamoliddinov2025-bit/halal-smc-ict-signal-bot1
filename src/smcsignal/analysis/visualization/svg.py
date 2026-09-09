"""Deterministic SVG rendering of a drawing model. Stdlib only, never raster."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.visualization.composer import price_domain
from smcsignal.analysis.visualization.models import (
    DrawingModel,
    EventMarker,
    IndicatorOverlay,
    LevelLine,
    StyleToken,
    TextAnnotation,
    ZoneRect,
)

COLORS: dict[StyleToken, str] = {
    StyleToken.BULLISH: "#2e7d32",
    StyleToken.BEARISH: "#c62828",
    StyleToken.NEUTRAL: "#616161",
    StyleToken.REFERENCE: "#1565c0",
    StyleToken.OVERLAY: "#6a1b9a",
}
MARGIN = 40.0


def render_svg(model: DrawingModel) -> str:
    """Render one standalone SVG document. Pure function of the model."""

    if not isinstance(model, DrawingModel):
        raise AnalysisInputError("rendering requires a DrawingModel")
    width = float(model.settings.svg_width)
    height = float(model.settings.svg_height)
    low, high = price_domain(model)
    count = len(model.candles)
    span = float(high - low)

    def x(index: int) -> float:
        step = (width - 2 * MARGIN) / count
        return MARGIN + index * step + step / 2

    def y(price: Decimal) -> float:
        fraction = float(price - low) / span
        return height - MARGIN - fraction * (height - 2 * MARGIN)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{model.settings.svg_width}" '
        f'height="{model.settings.svg_height}" '
        f'viewBox="0 0 {model.settings.svg_width} {model.settings.svg_height}">',
        f"  <title>{escape(model.symbol)} {escape(model.timeframe)} drawing</title>",
        f'  <rect width="{model.settings.svg_width}" '
        f'height="{model.settings.svg_height}" fill="#ffffff"/>',
        f'  <text x="{MARGIN}" y="20" font-family="monospace" font-size="13" fill="#212121">'
        f"{escape(model.symbol)} {escape(model.timeframe)}; candles {count}; not advice</text>",
        f'  <text x="{MARGIN}" y="{height - 10}" font-family="monospace" font-size="11" '
        f'fill="#212121">{escape(str(low))}</text>',
        f'  <text x="{width - MARGIN}" y="{height - 10}" text-anchor="end" '
        f'font-family="monospace" font-size="11" fill="#212121">{escape(str(high))}</text>',
    ]
    for observation in model.candles:
        candle = observation.candle
        color = "#2e7d32" if candle.close >= candle.open else "#c62828"
        body_top = y(max(candle.open, candle.close))
        body_bottom = y(min(candle.open, candle.close))
        body_height = max(body_bottom - body_top, 1.0)
        parts.append(
            f'  <line x1="{x(observation.reference.candle_index):.2f}" '
            f'y1="{y(candle.high):.2f}" x2="{x(observation.reference.candle_index):.2f}" '
            f'y2="{y(candle.low):.2f}" stroke="{color}" stroke-width="1"/>'
        )
        parts.append(
            f'  <rect x="{x(observation.reference.candle_index) - 2.5:.2f}" '
            f'y="{body_top:.2f}" width="5" height="{body_height:.2f}" fill="{color}"/>'
        )
    for primitive in model.primitives:
        if isinstance(primitive, LevelLine):
            parts.append(
                f'  <line x1="{x(primitive.start_index) - 2.5:.2f}" y1="{y(primitive.price):.2f}" '
                f'x2="{width - MARGIN:.2f}" y2="{y(primitive.price):.2f}" '
                f'stroke="{COLORS[primitive.token]}" stroke-dasharray="6 3" stroke-width="1"/>'
            )
        elif isinstance(primitive, ZoneRect):
            end = count - 1 if primitive.end_index is None else primitive.end_index
            left = x(primitive.start_index) - 3.0
            right = x(end) + 3.0
            top, bottom = y(primitive.upper), y(primitive.lower)
            parts.append(
                f'  <rect x="{left:.2f}" y="{top:.2f}" width="{max(right - left, 1.0):.2f}" '
                f'height="{max(bottom - top, 1.0):.2f}" fill="{COLORS[primitive.token]}" '
                f'fill-opacity="0.15" stroke="{COLORS[primitive.token]}" stroke-width="1"/>'
            )
        elif isinstance(primitive, EventMarker):
            parts.append(
                f'  <circle cx="{x(primitive.index):.2f}" cy="{y(primitive.price):.2f}" '
                f'r="4" fill="{COLORS[primitive.token]}"/>'
            )
            parts.append(
                f'  <text x="{x(primitive.index):.2f}" y="{y(primitive.price) - 7:.2f}" '
                f'text-anchor="middle" font-family="monospace" font-size="10" '
                f'fill="{COLORS[primitive.token]}">{escape(primitive.label)}</text>'
            )
        elif isinstance(primitive, TextAnnotation):
            parts.append(
                f'  <text x="{x(primitive.index):.2f}" y="{y(primitive.price) + 14:.2f}" '
                f'text-anchor="middle" font-family="monospace" font-size="10" '
                f'fill="{COLORS[primitive.token]}">{escape(primitive.text)}</text>'
            )
        elif isinstance(primitive, IndicatorOverlay):
            parts.append(
                f'  <rect x="{x(primitive.index) - 1.5:.2f}" y="{y(primitive.value) - 1.5:.2f}" '
                f'width="3" height="3" fill="{COLORS[primitive.token]}"/>'
            )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
