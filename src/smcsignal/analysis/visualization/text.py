"""Deterministic plain-text rendering of drawings and signal explanations."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.setup_attribution.models import SetupAttribution
from smcsignal.analysis.signal_engine.models import (
    SignalSnapshot,
    current_observation,
)
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

MARKS: dict[StyleToken, str] = {
    StyleToken.BULLISH: "^",
    StyleToken.BEARISH: "v",
    StyleToken.NEUTRAL: "o",
    StyleToken.REFERENCE: "*",
    StyleToken.OVERLAY: "+",
}


def _primitive_label(
    primitive: LevelLine | ZoneRect | EventMarker | TextAnnotation | IndicatorOverlay,
) -> str:
    if isinstance(primitive, TextAnnotation):
        return primitive.text
    return primitive.label


def render_text(model: DrawingModel) -> str:
    """Render the drawing as a deterministic character grid.

    Rows are price bands from the model domain; columns are candles. Levels,
    zones, markers, annotations, and overlays share the grid with candle
    closes. The legend always names the price domain and candle count.
    """

    if not isinstance(model, DrawingModel):
        raise AnalysisInputError("rendering requires a DrawingModel")
    rows = model.settings.text_rows
    low, high = price_domain(model)
    count = len(model.candles)
    span = high - low
    step = span / Decimal(rows)
    grid: list[list[str]] = [[" " for _ in range(count)] for _ in range(rows)]
    labels: list[str] = []

    def row_of(price: Decimal) -> int:
        offset = (price - low) / step
        row = rows - 1 - int(offset)
        return max(0, min(rows - 1, row))

    for observation in model.candles:
        candle = observation.candle
        grid[row_of(candle.close)][observation.reference.candle_index] = (
            "#" if candle.close >= candle.open else "."
        )
    for primitive in model.primitives:
        if isinstance(primitive, LevelLine):
            row = row_of(primitive.price)
            for column in range(primitive.start_index, count):
                if grid[row][column] == " ":
                    grid[row][column] = "-"
        elif isinstance(primitive, ZoneRect):
            end = count - 1 if primitive.end_index is None else primitive.end_index
            for row in range(row_of(primitive.upper), row_of(primitive.lower) + 1):
                for column in range(primitive.start_index, end + 1):
                    if grid[row][column] == " ":
                        grid[row][column] = "="
        elif isinstance(primitive, (EventMarker, TextAnnotation)):
            grid[row_of(primitive.price)][primitive.index] = MARKS[primitive.token]
        elif isinstance(primitive, IndicatorOverlay):
            row = row_of(primitive.value)
            if grid[row][primitive.index] in (" ", "-", "="):
                grid[row][primitive.index] = MARKS[primitive.token]
    for row in range(rows):
        band = high - Decimal(row) * step
        labels.append(f"{band} |" + "".join(grid[rows - 1 - row]))
    legend = sorted({_primitive_label(primitive) for primitive in model.primitives})
    lines = [
        f"{model.symbol} {model.timeframe}; candles {count}; not advice",
        *labels,
        "     " + "-" * count,
        "legend: # up close, . down close, - level, = zone, "
        "^ bullish, v bearish, o neutral, * reference, + overlay",
    ]
    if legend:
        lines.append("primitives: " + ", ".join(legend))
    return "\n".join(lines) + "\n"


def render_signal_explanation(
    frame: SignalSnapshot, attribution: SetupAttribution | None = None
) -> str:
    """Explain one published signal from its own published facts. Not advice."""

    if not isinstance(frame, SignalSnapshot):
        raise AnalysisInputError("explanations require a SignalSnapshot")
    if attribution is not None and not isinstance(attribution, SetupAttribution):
        raise AnalysisInputError("attribution must be a SetupAttribution or None")
    if attribution is not None and attribution.signal_id != frame.signal_id:
        raise AnalysisInputError("the attribution must explain this exact signal")
    observation = current_observation(frame.upstream)
    signal = frame.candidate.signal
    breakdown = frame.upstream.upstream.score.breakdown
    components = (
        "halal",
        "mtf",
        "mss",
        "displacement",
        "liquidity_sweep",
        "fvg",
        "order_block",
        "breaker_block",
        "mitigation_block",
        "premium_discount",
        "ote",
    )
    lines = [
        f"signal {frame.status.value} on {observation.reference.series.symbol} "
        f"{observation.reference.series.timeframe}",
        f"candle opened {observation.reference.opened_at.isoformat()}",
        f"score {signal.score_total} of publish threshold {signal.publish_threshold}",
        "components: " + ", ".join(f"{name} {getattr(breakdown, name)}" for name in components),
        "reasons: " + ", ".join(reason.value for reason in frame.candidate.reasons),
    ]
    if attribution is not None:
        lines.append(
            "setup labels: "
            + (
                "none observed"
                if not attribution.labels
                else ", ".join(label.value for label in attribution.labels)
            )
        )
        lines.append(f"combination: {attribution.combination_key or 'none'}")
    lines.append("descriptive explanation of published facts; not advice")
    return "\n".join(lines) + "\n"
