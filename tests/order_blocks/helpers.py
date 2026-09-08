"""Abstract synthetic candles for confirmed Order Block evidence."""

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.fvg import FVGConfig, analyze_fvg
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from smcsignal.analysis.order_blocks import (
    OrderBlockAnalyzer,
    OrderBlockConfig,
    analyze_order_blocks,
)
from tests.analysis.helpers import bar

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase7-fixture:v1:from-first:missing=error"
)
ANALYSIS = AnalysisConfig(3)
LIQUIDITY = LiquidityConfig("USDT")
DISPLACEMENT = DisplacementConfig(atr_period=3)
FVG = FVGConfig()


def upstream(
    candles,
    *,
    series=SERIES,
    analysis=ANALYSIS,
    liquidity=LIQUIDITY,
    displacement=DISPLACEMENT,
    fvg=FVG,
):
    base = analyze_liquidity(candles, series=series, config=liquidity, analysis_config=analysis)
    middle = analyze_displacement(base, displacement, price_unit=liquidity.price_unit)
    return analyze_fvg(middle, fvg)


def run(candles, *, displacement=DISPLACEMENT, **settings):
    return analyze_order_blocks(
        upstream(candles, displacement=displacement), OrderBlockConfig(**settings)
    )


def analyzer(**settings):
    return OrderBlockAnalyzer(OrderBlockConfig(**settings))


def bullish():
    rows = [
        (10, 11, 9, 10),
        (15, 16, 14, 15),
        (12, 13, 11, 12),
        (18, 19, 17, 18),
        (15, 15, 13, 14),
        (16, 17, 15, 16),
        (14, 22, 14, 20),
        (20, 23, 19, 22),
    ]
    return tuple(bar(i, c, opening=o, high=h, low=low) for i, (o, h, low, c) in enumerate(rows))


def choch():
    return (
        *bullish()[:7],
        bar(7, 19, opening=18, high=21, low=18),
        bar(8, 9, opening=19, high=20, low=8),
    )


def simple(*, candidates=(3,), confirmation=None):
    before = tuple(
        bar(i, 100, opening=101 if i in candidates else 100, high=101, low=99) for i in range(4)
    )
    return (*before, confirmation or bar(4, 105, opening=100, high=106, low=99))


def golden():
    initial = tuple(bar(i, 10, opening=10, high=11, low=9) for i in range(14))
    source = bullish()
    middle = tuple(
        bar(i + 14, c.close, opening=c.open, high=c.high, low=c.low) for i, c in enumerate(source)
    )
    return (
        *initial,
        *middle,
        bar(22, 9, opening=22, high=23, low=8),
        bar(23, 8, opening=9, high=12, low=7),
    )


def events(frames):
    return tuple(e for frame in frames for e in frame.events)
