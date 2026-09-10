from __future__ import annotations

from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.signal_engine import SignalStatus
from smcsignal.analysis.visualization import (
    EventMarker,
    IndicatorOverlay,
    LevelLine,
    StyleToken,
    TextAnnotation,
    VisualizationConfig,
    ZoneRect,
    compose_drawing,
    price_domain,
)
from tests.outcome_tracking.helpers import signal_frames
from tests.visualization.helpers import (
    impulse_model,
    model,
    ote_model,
    streams,
)


def by_type(drawing, kind):
    return [item for item in drawing.primitives if isinstance(item, kind)]


def test_sweep_fixture_projects_every_published_fact() -> None:
    drawing = model()
    assert (drawing.symbol, drawing.timeframe) == ("BTCUSDT", "15m")
    assert len(drawing.candles) == 18
    levels = by_type(drawing, LevelLine)
    assert sorted(str(line.price) for line in levels) == ["21", "23"]
    assert all(line.token is StyleToken.NEUTRAL for line in levels)
    zones = by_type(drawing, ZoneRect)
    assert [(zone.lower, zone.upper, zone.start_index, zone.label) for zone in zones] == [
        (Decimal("26"), Decimal("31"), 14, "fvg bullish")
    ]
    markers = by_type(drawing, EventMarker)
    sweeps = [m for m in markers if m.label.startswith("liquidity sweep")]
    assert [(m.index, m.price) for m in sweeps] == [(12, Decimal("21"))]
    buys = [m for m in markers if m.label == "buy signal"]
    assert [m.index for m in buys] == [4, 8, 12, 13, 14, 16]
    displacements = [m for m in markers if m.label.startswith("displacement")]
    assert [(m.index, m.token) for m in displacements] == [(13, StyleToken.BULLISH)]
    annotations = by_type(drawing, TextAnnotation)
    assert [note.text for note in annotations] == [
        "score 25",
        "score 25",
        "score 35",
        "score 37",
        "score 33",
        "score 25",
    ]


def test_buy_markers_exist_only_on_buy_frames() -> None:
    primary, frames, indicators, outcomes = streams()
    drawing = compose_drawing(primary, frames, indicators, outcomes)
    buy_indexes = {
        frame.candidate.signal.candle.candle_index
        for frame in frames
        if frame.status is SignalStatus.BUY_SIGNAL
    }
    marker_indexes = {
        marker.index for marker in by_type(drawing, EventMarker) if marker.label == "buy signal"
    }
    assert marker_indexes == buy_indexes == {4, 8, 12, 13, 14, 16}


def test_indicator_overlays_are_price_scaled_only() -> None:
    drawing = model()
    overlays = by_type(drawing, IndicatorOverlay)
    labels = {overlay.label for overlay in overlays}
    assert labels == {"ema3", "ema5", "atr"}
    assert "rsi" not in labels and "volume" not in labels
    assert all(overlay.token is StyleToken.OVERLAY for overlay in overlays)
    ema3 = [o for o in overlays if o.label == "ema3"]
    assert [o.index for o in ema3][:3] == [2, 3, 4]  # first value at period - 1
    atr = [o for o in overlays if o.label == "atr"]
    assert [o.index for o in atr][:2] == [3, 4]  # Phase 5 ATR reuse


def test_primary_only_drawing_has_no_markers_or_overlays() -> None:
    primary, _, _, _ = streams()
    drawing = compose_drawing(primary)
    assert by_type(drawing, EventMarker) or by_type(drawing, LevelLine)  # facts remain
    assert not [marker for marker in by_type(drawing, EventMarker) if marker.label == "buy signal"]
    assert by_type(drawing, IndicatorOverlay) == []
    assert by_type(drawing, TextAnnotation) == []


def test_impulse_fixture_covers_ob_dealing_range_and_ote() -> None:
    drawing = impulse_model()
    zones = by_type(drawing, ZoneRect)
    labels = {zone.label for zone in zones}
    assert "order block bullish" in labels
    assert "dealing range" in labels
    assert "ote zone bullish" in labels or "ote zone bearish" in labels
    assert "fvg bullish" in labels
    levels = {line.label for line in by_type(drawing, LevelLine)}
    assert "equilibrium" in levels


def test_ote_fixture_covers_bearish_fvg_and_direction_tokens() -> None:
    drawing = ote_model()
    zones = by_type(drawing, ZoneRect)
    bearish = [zone for zone in zones if zone.label == "fvg bearish"]
    assert [zone.start_index for zone in bearish] == [21, 22]
    assert all(zone.token is StyleToken.BEARISH for zone in bearish)


def test_pool_versions_draw_one_level_each() -> None:
    drawing = model()
    pools = [line for line in by_type(drawing, LevelLine) if line.label.startswith("liquidity")]
    assert len(pools) == len({line.price for line in pools}) == 2


def test_price_domain_includes_primitive_prices() -> None:
    drawing = model()
    low, high = price_domain(drawing)
    assert low == Decimal("19")  # the lowest candle low
    assert high == Decimal("37")  # the highest candle high


def test_composition_is_deterministic() -> None:
    first = model()
    second = model()
    assert first == second
    assert first.primitives == second.primitives


def test_composer_rejects_broken_inputs() -> None:
    primary, frames, indicators, outcomes = streams()
    with pytest.raises(AnalysisInputError):
        compose_drawing(())
    with pytest.raises(AnalysisInputError):
        compose_drawing(primary[:-1], signals=frames)  # misaligned signals
    with pytest.raises(AnalysisInputError):
        compose_drawing(primary, signals=frames[:-1])
    with pytest.raises(AnalysisInputError):
        compose_drawing(primary, indicators=indicators[:-2])
    with pytest.raises(AnalysisInputError):
        compose_drawing((frames[0],))  # not OTE frames
    with pytest.raises(AnalysisInputError):
        compose_drawing(primary, config="strict")  # type: ignore[arg-type]


def test_composer_rejects_foreign_series_streams() -> None:
    from tests.halal_filter.helpers import series
    from tests.mtf.helpers import ote_frames
    from tests.outcome_tracking.helpers import signal_frames
    from tests.setup_attribution.helpers import sweep_candles

    candles = sweep_candles()
    frames = signal_frames(candles)
    foreign = ote_frames(candles, "15m", series=series("BTCUSDT", "15m"))
    assert len(foreign) == len(frames)
    with pytest.raises(AnalysisInputError):
        compose_drawing(foreign, frames)  # a different dataset identity


def test_composer_rejects_non_consecutive_primary_replays() -> None:
    primary, _, _, _ = streams()
    with pytest.raises(AnalysisInputError):
        compose_drawing(primary[1:])
    with pytest.raises(AnalysisInputError):
        compose_drawing(primary[::-1])


def test_config_size_choices_travel_with_the_model() -> None:
    drawing = model(config=VisualizationConfig(svg_width=640, svg_height=320, text_rows=12))
    assert drawing.settings == VisualizationConfig(svg_width=640, svg_height=320, text_rows=12)


def test_outcome_markers_exist_only_for_finalized_records() -> None:
    drawing = model()
    markers = [
        marker for marker in by_type(drawing, EventMarker) if marker.label.startswith("outcome ")
    ]
    # The 18-candle sweep replay finalizes exactly one outcome: the BUY at
    # candle 4 wins at its horizon candle 14 (close 33).
    assert [(m.index, m.price, m.label, m.token) for m in markers] == [
        (14, Decimal("33"), "outcome WIN", StyleToken.BULLISH)
    ]
    primary, frames, indicators, _ = streams()
    open_only = compose_drawing(primary[:13], signals=frames[:13], indicators=indicators[:13])
    assert not [
        marker for marker in by_type(open_only, EventMarker) if marker.label.startswith("outcome ")
    ]


def test_outcome_tokens_follow_the_final_status() -> None:
    from smcsignal.analysis.outcome_tracking import (
        OutcomeTrackingConfig,
        analyze_outcome_tracking,
    )
    from tests.mtf.helpers import ote_frames
    from tests.outcome_tracking.helpers import FALLING_TAIL, candles_for

    candles = candles_for(FALLING_TAIL)
    frames = signal_frames(candles)
    outcomes = analyze_outcome_tracking(frames, OutcomeTrackingConfig())
    primary = ote_frames(candles, "15m")
    drawing = compose_drawing(primary, signals=frames, outcomes=outcomes)
    markers = [
        marker for marker in by_type(drawing, EventMarker) if marker.label.startswith("outcome ")
    ]
    assert [(m.label, m.token) for m in markers] == [("outcome LOSS", StyleToken.BEARISH)]
