"""Reuse existing MTF fixtures; prices are not reclassified by the registry."""

from smcsignal.analysis import SeriesProvenance
from smcsignal.analysis.halal_filter import HalalFilterAnalyzer, HalalFilterConfig, analyze_halal
from smcsignal.analysis.mtf import analyze_mtf
from tests.mtf.helpers import (
    EIGHT,
    FOUR_HOUR_PRICES,
    HOUR_PRICES,
    MIDNIGHT,
    PRIMARY_PRICES,
    bars,
    ote_frames,
)
from tests.mtf.helpers import run as mtf_run


def series(symbol, timeframe, dataset=None):
    return SeriesProvenance(
        symbol,
        timeframe,
        "synthetic_spot",
        "csv",
        dataset or f"phase14-fixture:{symbol}:{timeframe}:v1",
    )


def mtf_for(symbol="BTCUSDT"):
    if symbol == "BTCUSDT":
        return mtf_run()
    primary = ote_frames(
        bars("15m", PRIMARY_PRICES, start=EIGHT),
        "15m",
        series=series(symbol, "15m"),
    )
    hourly = ote_frames(
        bars("1h", HOUR_PRICES, start=MIDNIGHT),
        "1h",
        series=series(symbol, "1h"),
    )
    four = ote_frames(
        bars("4h", FOUR_HOUR_PRICES, start=EIGHT),
        "4h",
        series=series(symbol, "4h"),
    )
    return analyze_mtf(primary, {"1h": hourly, "4h": four})


def run(frames=None, config=None):
    return analyze_halal(
        frames if frames is not None else mtf_for(),
        config if config is not None else HalalFilterConfig(),
    )


def analyzer(config=None):
    return HalalFilterAnalyzer(config if config is not None else HalalFilterConfig())
