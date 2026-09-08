"""Hand-auditable synthetic closed candles; no market performance claims."""

from decimal import Decimal

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.displacement import (
    DisplacementAnalyzer,
    DisplacementConfig,
    analyze_displacement,
)
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from tests.analysis.helpers import bar

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase5-fixture:v1:from-first:missing=error"
)
LIQUIDITY = LiquidityConfig("USDT")
ANALYSIS = AnalysisConfig(3)


def settings(**overrides):
    values = {"atr_period": 3, **overrides}
    for name in (
        "min_body_atr",
        "min_range_atr",
        "bullish_close_min",
        "bearish_close_max",
        "atr_floor",
    ):
        if name in values and isinstance(values[name], str):
            values[name] = Decimal(values[name])
    return DisplacementConfig(**values)


def upstream(candles, *, series=SERIES, liquidity=LIQUIDITY, analysis=ANALYSIS):
    return analyze_liquidity(candles, series=series, config=liquidity, analysis_config=analysis)


def run(candles, **overrides):
    return analyze_displacement(upstream(candles), settings(**overrides), price_unit="USDT")


def analyzer(**overrides):
    return DisplacementAnalyzer(settings(**overrides), price_unit="USDT")


def warmup(period=3, *, flat=False):
    return tuple(
        bar(i, 100, opening=100, high=100 if flat else 101, low=100 if flat else 99)
        for i in range(period + 1)
    )


def bull(index=4):
    # ATR=2: body=2, range=3, bullish close-location exactly .70.
    return bar(index, 102, opening=100, high="102.9", low="99.9")


def bear(index=4):
    # ATR=2: body=2, range=3, bearish close-location exactly .30.
    return bar(index, 98, opening=100, high="100.1", low="97.1")


def golden():
    return (
        *warmup(14),
        bar(15, 103, opening=100, high=104, low=99),
        bar(16, 98, opening=103, high=104, low=97),
        bar(17, 98, opening=98, high=100, low=96),
        bar(18, "98.5", opening=98, high=110, low=97),
        bar(19, 121, opening=120, high=122, low=119),
    )


def events(frames):
    return tuple(event for frame in frames for event in frame.events)
