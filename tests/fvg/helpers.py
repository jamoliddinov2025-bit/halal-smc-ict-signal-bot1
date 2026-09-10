"""Synthetic fixtures for FVG geometry; no returns, trades, or real-market claims."""

from decimal import Decimal

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.fvg import FVGAnalyzer, FVGConfig, analyze_fvg
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from tests.analysis.helpers import bar
from tests.displacement.helpers import warmup

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase6-fixture:v1:from-first:missing=error"
)
LIQUIDITY = LiquidityConfig("USDT")
ANALYSIS = AnalysisConfig(3)
DISPLACEMENT = DisplacementConfig(atr_period=3)


def upstream(
    candles, *, series=SERIES, liquidity=LIQUIDITY, analysis=ANALYSIS, displacement=DISPLACEMENT
):
    frames = analyze_liquidity(candles, series=series, config=liquidity, analysis_config=analysis)
    return analyze_displacement(frames, displacement, price_unit=liquidity.price_unit)


def settings(minimum="0", require=False):
    return FVGConfig(Decimal(minimum), require)


def run(candles, *, minimum="0", require=False, displacement=DISPLACEMENT):
    return analyze_fvg(upstream(candles, displacement=displacement), settings(minimum, require))


def analyzer(minimum="0", require=False):
    return FVGAnalyzer(settings(minimum, require))


def bullish():
    return (
        bar(0, 101, opening=100, high=102, low=99),
        bar(1, 104, opening=101, high=105, low=100),
        bar(2, 105, opening=104, high=106, low=103),
    )


def associated(*, opposed=False):
    middle = (
        bar(2, 102, opening=107, high=108, low=101)
        if opposed
        else bar(2, 105, opening=100, high=106, low=99)
    )
    return (*warmup(1), middle, bar(3, 107, opening=105, high=108, low=104))


def golden():
    return (
        *warmup(14),
        bar(15, 105, opening=100, high=106, low=99),
        bar(16, 107, opening=105, high=108, low=104),
        bar(17, 108, opening=107, high=109, low=106),
        bar(18, 96, opening=108, high=109, low=95),
        bar(19, 94, opening=96, high=98, low=93),
    )


def events(frames):
    return tuple(e for f in frames for e in f.events)
