from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from smcsignal.analysis.indicators import analyze_indicators
from smcsignal.analysis.outcome_tracking import (
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)
from smcsignal.analysis.visualization import (
    DrawingModel,
    EventMarker,
    IndicatorOverlay,
    LevelLine,
    TextAnnotation,
    ZoneRect,
    compose_drawing,
    render_svg,
    render_text,
)
from tests.mtf.helpers import ote_frames
from tests.outcome_tracking.helpers import signal_frames
from tests.setup_attribution.helpers import SWEEP_ROWS, row_candles, sweep_candles
from tests.visualization.helpers import SMALL, model

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"


def build(candles):
    """The full visualization input stack over one candle replay."""

    frames = signal_frames(candles)
    primary = ote_frames(candles, "15m")
    from smcsignal.analysis.setup_quality.calculation import nested_displacement

    displacement = tuple(nested_displacement(frame.upstream.upstream.upstream) for frame in frames)
    indicators = analyze_indicators(displacement, SMALL)
    outcomes = analyze_outcome_tracking(frames, OutcomeTrackingConfig())
    return primary, frames, indicators, outcomes


def _indices_of(primitive) -> tuple[int, ...]:
    if isinstance(primitive, LevelLine):
        return (primitive.start_index,)
    if isinstance(primitive, ZoneRect):
        return (
            (primitive.start_index,)
            if primitive.end_index is None
            else (
                primitive.start_index,
                primitive.end_index,
            )
        )
    return (primitive.index,)


def _prefix_primitives(drawing: DrawingModel, cut: int):
    return tuple(p for p in drawing.primitives if max(_indices_of(p)) < cut)


def test_prefix_replays_reproduce_prefix_drawings_exactly() -> None:
    primary, frames, indicators, outcomes = build(sweep_candles())
    full = compose_drawing(primary, signals=frames, indicators=indicators, outcomes=outcomes)
    for cut in (6, 10, 14, 18):
        prefix = compose_drawing(
            primary[:cut],
            signals=frames[:cut],
            indicators=indicators[:cut],
            outcomes=outcomes[:cut],
        )
        assert prefix.candles == full.candles[:cut]
        assert _prefix_primitives(prefix, cut) == _prefix_primitives(full, cut)


def test_future_candles_never_change_past_primitives() -> None:
    head = SWEEP_ROWS[:14]
    rising = SWEEP_ROWS[14:]
    falling = (
        (32, 33, 30, 31, 6),
        (31, 32, 29, 30, 6),
        (30, 31, 28, 29, 6),
        (29, 30, 27, 28, 6),
    )
    base = compose_drawing(*build(row_candles(head + rising)))
    other = compose_drawing(*build(row_candles(head + falling)))
    assert _prefix_primitives(base, 14) == _prefix_primitives(other, 14)
    assert base.drawing_id != other.drawing_id  # the futures legitimately differ


def test_drawing_is_a_pure_function_of_its_inputs() -> None:
    primary, frames, indicators, outcomes = build(sweep_candles())
    first = compose_drawing(primary, signals=frames, indicators=indicators, outcomes=outcomes)
    second = compose_drawing(
        deepcopy(primary),
        signals=deepcopy(frames),
        indicators=deepcopy(indicators),
        outcomes=deepcopy(outcomes),
    )
    assert first == second and first.drawing_id == second.drawing_id


def test_indicator_overlays_use_only_already_published_values() -> None:
    drawing = model()
    for overlay in (item for item in drawing.primitives if isinstance(item, IndicatorOverlay)):
        # Warmup candles never carry overlay points; the first EMA(3) point is
        # at candle 2 and the first ATR at candle 3.
        if overlay.label == "ema3":
            assert overlay.index >= 2
        if overlay.label == "ema5":
            assert overlay.index >= 4
        if overlay.label == "atr":
            assert overlay.index >= 3


def test_rendering_is_a_pure_function_of_the_model() -> None:
    drawing = model()
    saved = deepcopy(drawing)
    assert render_text(drawing) == render_text(saved)
    assert render_svg(drawing) == render_svg(saved)
    assert drawing == saved


def test_visualization_never_runs_a_detector() -> None:
    """The layer reads published frames; it never re-detects anything."""

    for module in sorted((PACKAGE / "visualization").glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "analyze_" not in text, f"{module.name} must not call analyzers"
        assert "Analyzer" not in text, f"{module.name} must not construct analyzers"


def test_visualization_reads_no_outcome_reclassification() -> None:
    """Outcome markers copy published statuses; WIN/LOSS is never recomputed."""

    for module in sorted((PACKAGE / "visualization").glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "classify_outcome" not in text, f"{module.name} must not classify"
        assert "final_difference" not in text, f"{module.name} must not reclassify"
    drawing = model()
    outcome_markers = [
        marker
        for marker in drawing.primitives
        if isinstance(marker, EventMarker) and marker.label.startswith("outcome ")
    ]
    assert [marker.label for marker in outcome_markers] == ["outcome WIN"]


def test_open_outcomes_and_unpublished_signals_draw_nothing_future() -> None:
    """A prefix drawing cannot show an outcome that finalizes later."""

    primary, frames, indicators, outcomes = build(sweep_candles())
    before_final = compose_drawing(
        primary[:14],
        signals=frames[:14],
        indicators=indicators[:14],
        outcomes=outcomes[:14],
    )
    assert not [
        marker
        for marker in before_final.primitives
        if isinstance(marker, EventMarker) and marker.label.startswith("outcome ")
    ]
    after_final = compose_drawing(
        primary[:15],
        signals=frames[:15],
        indicators=indicators[:15],
        outcomes=outcomes[:15],
    )
    assert [
        marker.label
        for marker in after_final.primitives
        if isinstance(marker, EventMarker) and marker.label.startswith("outcome ")
    ] == ["outcome WIN"]


def test_buy_annotations_come_only_from_published_buy_frames() -> None:
    primary, frames, indicators, outcomes = build(sweep_candles())
    drawing = compose_drawing(primary, signals=frames, indicators=indicators, outcomes=outcomes)
    annotations = [item for item in drawing.primitives if isinstance(item, TextAnnotation)]
    buy_indexes = {
        frame.candidate.signal.candle.candle_index
        for frame in frames
        if frame.status.value == "BUY_SIGNAL"
    }
    assert {note.index for note in annotations} == buy_indexes
    for note in annotations:
        assert note.text.startswith("score ")
