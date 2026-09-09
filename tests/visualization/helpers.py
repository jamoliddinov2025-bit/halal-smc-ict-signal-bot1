"""Reuse existing chain fixtures; visualization re-detects nothing."""

from __future__ import annotations

from smcsignal.analysis.indicators import IndicatorsConfig, analyze_indicators
from smcsignal.analysis.outcome_tracking import (
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)
from smcsignal.analysis.setup_quality.calculation import nested_displacement
from smcsignal.analysis.visualization import (
    VisualizationConfig,
    compose_drawing,
)
from tests.mtf.helpers import ote_frames
from tests.outcome_tracking.helpers import signal_frames
from tests.setup_attribution.helpers import impulse_candles, ote_candles, sweep_candles

SMALL = IndicatorsConfig(ema_periods=(3, 5), rsi_period=3, volume_average_period=4)


def streams(candles=None):
    """(primary, signals, indicators, outcomes) over one finished replay."""

    candles = sweep_candles() if candles is None else candles
    frames = signal_frames(candles)
    primary = ote_frames(candles, "15m")
    displacement = tuple(nested_displacement(frame.upstream.upstream.upstream) for frame in frames)
    indicators = analyze_indicators(displacement, SMALL)
    outcomes = analyze_outcome_tracking(frames, OutcomeTrackingConfig())
    return primary, frames, indicators, outcomes


def model(candles=None, config=None):
    primary, frames, indicators, outcomes = streams(candles)
    return compose_drawing(
        primary,
        signals=frames,
        indicators=indicators,
        outcomes=outcomes,
        config=config if config is not None else VisualizationConfig(),
    )


def impulse_model():
    primary, frames, indicators, outcomes = streams(impulse_candles())
    return compose_drawing(primary, signals=frames, indicators=indicators, outcomes=outcomes)


def ote_model():
    primary, frames, indicators, outcomes = streams(ote_candles())
    return compose_drawing(primary, signals=frames, indicators=indicators, outcomes=outcomes)


__all__ = ["SMALL", "impulse_model", "model", "ote_model", "streams"]
