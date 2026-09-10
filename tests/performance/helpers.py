"""Reuse existing Phase 3-19b fixtures; performance recomputes, never reclassifies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from smcsignal.analysis import HalalFilterConfig, analyze_halal
from smcsignal.analysis.mtf import MTFConfig, analyze_mtf
from smcsignal.analysis.outcome_tracking import (
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)
from smcsignal.analysis.performance import (
    PerformanceConfig,
    analyze_performance,
)
from smcsignal.analysis.setup_attribution import analyze_setup_attribution
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_engine import analyze_signal_engine
from tests.halal_filter.helpers import series
from tests.mtf.helpers import (
    EIGHT,
    FOUR_HOUR_PRICES,
    HOUR_PRICES,
    MIDNIGHT,
    bar_at,
    bars,
    ote_frames,
)
from tests.setup_attribution.helpers import SWEEP_ROWS, row_candles
from tests.signal_engine.helpers import matching_config

STEP = timedelta(minutes=15)
FEBRUARY = datetime(2024, 2, 1, 8, tzinfo=UTC)
LONG_RISING = tuple(range(20, 54))  # 34 candles; every BUY reaches the horizon
# The sweep fixture extended so the displacement BUY also finalizes.
SWEEP_LONG_ROWS = SWEEP_ROWS + tuple(
    (close + 1, close + 2, close - 1, close, 5) for close in (37, 38, 39, 40, 41, 42)
)


def engine_frames(candles, symbol="BTCUSDT"):
    """The standard Phase 3-17 chain for any symbol and candle base."""

    primary = ote_frames(candles, "15m", series=series(symbol, "15m"))
    hourly = ote_frames(bars("1h", HOUR_PRICES, start=MIDNIGHT), "1h", series=series(symbol, "1h"))
    four = ote_frames(bars("4h", FOUR_HOUR_PRICES, start=EIGHT), "4h", series=series(symbol, "4h"))
    merged = analyze_mtf(primary, {"1h": hourly, "4h": four}, MTFConfig())
    halal = analyze_halal(merged, HalalFilterConfig())
    scored = analyze_setup_quality(halal, SetupQualityConfig(10))
    eligible = analyze_signal_eligibility(scored, SignalEligibilityConfig())
    return analyze_signal_engine(eligible, matching_config(eligible[0]))


def candles_at(base, prices):
    return tuple(bar_at(base + index * STEP, close) for index, close in enumerate(prices))


def replay(candles, symbol="BTCUSDT", horizon=None):
    """One finished replay: (outcome frames, attribution frames)."""

    frames = engine_frames(candles, symbol)
    config = (
        OutcomeTrackingConfig() if horizon is None else OutcomeTrackingConfig(horizon_bars=horizon)
    )
    return analyze_outcome_tracking(frames, config), analyze_setup_attribution(frames)


def rising_replay():

    return replay(candles_at(EIGHT, tuple(range(20, 37))))


def flat_replay(horizon=None):
    from tests.outcome_tracking.helpers import FLAT_TAIL, candles_for

    return replay(candles_for(FLAT_TAIL), horizon=horizon)


def falling_replay():
    from tests.outcome_tracking.helpers import FALLING_TAIL, candles_for

    return replay(candles_for(FALLING_TAIL))


def sweep_long_replay():
    return replay(row_candles(SWEEP_LONG_ROWS))


def january_long_replay():
    return replay(candles_at(EIGHT, LONG_RISING))


def february_replay():
    return replay(candles_at(FEBRUARY, LONG_RISING))


def eth_replay():
    return replay(candles_at(EIGHT, tuple(range(20, 37))), symbol="ETHUSDT")


def run(outcomes=None, attributions=None, config=None):
    if outcomes is None:
        replayed_outcomes, replayed_attributions = rising_replay()
        outcomes = {"btc": replayed_outcomes}
        if attributions is None:
            attributions = {"btc": replayed_attributions}
    return analyze_performance(
        outcomes,
        attributions,
        config if config is not None else PerformanceConfig(),
    )


__all__ = [
    "FEBRUARY",
    "LONG_RISING",
    "SWEEP_LONG_ROWS",
    "candles_at",
    "engine_frames",
    "eth_replay",
    "falling_replay",
    "february_replay",
    "flat_replay",
    "january_long_replay",
    "replay",
    "rising_replay",
    "run",
    "sweep_long_replay",
]
