"""Presentation-side chart integration for Phase 24.

This module only *consumes* an already-created Phase 19e ``DrawingModel``. It
does not re-create or re-render drawings from raw facts, and it does not add an
SVG-to-PNG converter (that conversion is deferred to a later phase). A chart
that does not reference the same series as the signal is rejected; a chart
failure handled by the caller must never mutate or invalidate the signal/message.
"""

from __future__ import annotations

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.visualization import DrawingModel, render_svg

from .models import ChartAttachment, chart_artifact_id

SVG_MIME = "image/svg+xml"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def _bind_series(
    drawing: DrawingModel,
    symbol: str,
    timeframe: str,
) -> None:
    if not isinstance(drawing, DrawingModel):
        raise AnalysisInputError("chart requires an existing DrawingModel")
    _text(symbol, "symbol")
    _text(timeframe, "timeframe")
    if drawing.symbol != symbol or drawing.timeframe != timeframe:
        raise AnalysisInputError(
            "drawing series must match the signal series "
            f"({drawing.symbol} {drawing.timeframe} != {symbol} {timeframe})"
        )


def svg_attachment(
    drawing: DrawingModel,
    *,
    signal_id: str,
    symbol: str,
    timeframe: str,
) -> ChartAttachment:
    """Build a deterministic SVG chart artifact bound to one signal.

    Raises ``AnalysisInputError`` if the drawing does not belong to the signal's
    series. The artifact identity and filename are derived from the drawing's
    deterministic ``drawing_id`` and the signal id; nothing here depends on the
    clock or on randomness.
    """
    _bind_series(drawing, symbol, timeframe)
    _text(signal_id, "signal_id")
    svg = render_svg(drawing)
    drawing_id = drawing.drawing_id
    stem = drawing_id.removeprefix("drawing:")
    return ChartAttachment(
        signal_id=signal_id,
        drawing_id=drawing_id,
        mime_type=SVG_MIME,
        filename=f"{stem}.svg",
        content=svg,
        artifact_id=chart_artifact_id(signal_id, drawing_id, SVG_MIME),
    )
