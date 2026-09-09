"""Reuse existing Phase 3-17 chain fixtures; attribution never reruns detectors."""

from __future__ import annotations

from datetime import timedelta

from smcsignal.analysis import HalalFilterConfig, analyze_halal
from smcsignal.analysis.mtf import MTFConfig, analyze_mtf
from smcsignal.analysis.setup_attribution import (
    SetupAttributionAnalyzer,
    SetupAttributionConfig,
    analyze_setup_attribution,
)
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_engine import analyze_signal_engine
from tests.analysis.helpers import mirrored
from tests.halal_filter.helpers import series
from tests.mtf.helpers import (
    BEARISH_HTF,
    BULLISH_HTF,
    EIGHT,
    MIDNIGHT,
    bar_at,
    bars,
    ote_frames,
)
from tests.order_blocks.helpers import bullish
from tests.outcome_tracking.helpers import signal_frames  # noqa: F401  (chain re-export)
from tests.signal_engine.helpers import matching_config

STEP = timedelta(minutes=15)

# Uptrend, equal pivot lows at 23, sweep candle (low 21, close back above the
# pool), then a bullish displacement candle whose event carries the sweep.
SWEEP_ROWS = (
    (19, 21, 19, 20, 5),
    (20, 22, 20, 21, 5),
    (21, 23, 21, 22, 5),
    (22, 24, 22, 23, 5),
    (23, 25, 23, 24, 5),
    (24, 26, 24, 25, 5),
    (25, 27, 25, 26, 5),
    (26, 28, 26, 27, 5),
    (27, 28, 23, 25, 6),
    (25, 27, 25, 26, 5),
    (26, 27, 23, 24, 5),
    (24, 26, 24, 25, 5),
    (25, 26, 21, 25.5, 7),
    (25.5, 33, 25, 32, 9),
    (32, 34, 31, 33, 6),
    (33, 35, 32, 34, 6),
    (34, 36, 33, 35, 6),
    (35, 37, 34, 36, 6),
)

# Phase 7 bullish order-block pattern plus a retracing tail.
IMPULSE_ROWS = (
    (10, 11, 9, 10, 5),
    (15, 16, 14, 15, 5),
    (12, 13, 11, 12, 5),
    (18, 19, 17, 18, 5),
    (15, 15, 13, 14, 5),
    (16, 17, 15, 16, 5),
    (14, 22, 14, 20, 9),
    (20, 23, 19, 22, 7),
) + tuple((close + 1, close + 2, close - 1, close, 4) for close in (19, 18, 17, 16, 15, 14, 13))

# Flat warmup, displacement impulse, then a retracement into OTE and discount.
OTE_ROWS = (
    tuple((100, 101, 99, 100, 5) for _ in range(15))
    + (
        (100, 104, 99, 103, 6),
        (103, 104, 97, 98, 6),
        (98, 100, 96, 98, 6),
        (98, 110, 97, 98.5, 9),
        (110, 112, 105, 108, 7),
    )
    + tuple((close + 2, close + 3, close - 1, close, 4) for close in (104, 101, 99, 98, 97, 96, 95))
)


def row_candles(rows, start_index=0):
    """OHLC rows (open, high, low, close, volume) on the 15m EIGHT base."""

    return tuple(
        bar_at(
            EIGHT + (start_index + offset) * STEP,
            close,
            opening=open,
            high=high,
            low=low,
            volume=volume,
        )
        for offset, (open, high, low, close, volume) in enumerate(rows)
    )


def sweep_candles():
    return row_candles(SWEEP_ROWS)


def impulse_candles():
    return row_candles(IMPULSE_ROWS)


def ote_candles():
    return row_candles(OTE_ROWS)


def mirrored_ob_frames():
    """The mirrored Phase 7 pattern; bearish labels never become BUY profiles."""

    return signal_frames(mirrored(bullish()))


def mixed_frames():
    """Rising primary with one bullish and one bearish ready higher timeframe."""

    primary = ote_frames(
        bars("15m", tuple(range(20, 37)), start=EIGHT),
        "15m",
        series=series("BTCUSDT", "15m"),
    )
    hourly = ote_frames(
        bars("1h", BULLISH_HTF, start=MIDNIGHT), "1h", series=series("BTCUSDT", "1h")
    )
    half = ote_frames(
        bars("30m", BEARISH_HTF, start=MIDNIGHT), "30m", series=series("BTCUSDT", "30m")
    )
    merged = analyze_mtf(
        primary, {"1h": hourly, "30m": half}, MTFConfig(higher_timeframes=("1h", "30m"))
    )
    halal = analyze_halal(merged, HalalFilterConfig())
    scored = analyze_setup_quality(halal, SetupQualityConfig(10))
    eligible = analyze_signal_eligibility(scored, SignalEligibilityConfig())
    return analyze_signal_engine(eligible, matching_config(eligible[0]))


def run(frames=None, config=None):
    return analyze_setup_attribution(
        frames if frames is not None else signal_frames(),
        config if config is not None else SetupAttributionConfig(),
    )


def analyzer(config=None):
    return SetupAttributionAnalyzer(config if config is not None else SetupAttributionConfig())


__all__ = [
    "IMPULSE_ROWS",
    "OTE_ROWS",
    "SWEEP_ROWS",
    "analyzer",
    "impulse_candles",
    "mirrored_ob_frames",
    "mixed_frames",
    "ote_candles",
    "row_candles",
    "run",
    "signal_frames",
    "sweep_candles",
]
