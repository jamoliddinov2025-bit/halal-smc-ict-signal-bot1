"""Synthetic observations for strict source-OB conversion, never market forecasts."""

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.breaker_blocks import (
    BreakerBlockAnalyzer,
    BreakerBlockConfig,
    analyze_breaker_blocks,
)
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.fvg import FVGConfig, analyze_fvg
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from smcsignal.analysis.mss import MSSConfig, analyze_mss
from smcsignal.analysis.order_blocks import (
    OrderBlockConfig,
    StructureRequirement,
    analyze_order_blocks,
)
from smcsignal.analysis.premium_discount import PDConfig, analyze_pd
from tests.analysis.helpers import bar
from tests.mss.helpers import consecutive as mss_consecutive
from tests.order_blocks.helpers import bullish as bullish_ob

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase10-fixture:v1:from-first:missing=error"
)
ANALYSIS = AnalysisConfig(3)
LIQUIDITY = LiquidityConfig("USDT")
DISPLACEMENT = DisplacementConfig(atr_period=3)
FVG = FVGConfig()
OB = OrderBlockConfig()
PD = PDConfig()
MSS = MSSConfig()
DISPLACEMENT_ONLY = OrderBlockConfig(structure_requirement=StructureRequirement.DISPLACEMENT_ONLY)


def upstream(
    candles,
    *,
    series=SERIES,
    analysis=ANALYSIS,
    liquidity=LIQUIDITY,
    displacement=DISPLACEMENT,
    fvg=FVG,
    order_blocks=OB,
    pd=PD,
):
    a = analyze_liquidity(candles, series=series, config=liquidity, analysis_config=analysis)
    b = analyze_displacement(a, displacement, price_unit=liquidity.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, fvg), order_blocks)
    return analyze_mss(analyze_pd(c, pd), MSS)


def run(candles, **kwargs):
    return analyze_breaker_blocks(upstream(candles, **kwargs))


def analyzer():
    return BreakerBlockAnalyzer(BreakerBlockConfig())


def base():
    return (
        *bullish_ob()[:7],
        bar(7, 17, opening=16, high=18, low=16),
        bar(8, 9, opening=16, high=16, low=8),
        bar(9, 16, opening=16, high=17, low=15),
        bar(10, 7, opening=7, high=8, low=6),
        bar(11, 14, opening=14, high=15, low=13),
        bar(12, 8, opening=9, high=9, low=7),
        bar(13, 30, opening=16, high=31, low=16),
    )


def golden():
    initial = tuple(bar(i, 10, opening=10, high=11, low=9) for i in range(14))
    return (
        *initial,
        *(
            bar(i + 14, c.close, opening=c.open, high=c.high, low=c.low)
            for i, c in enumerate(base())
        ),
    )


def multiple(*, overlap=False, first_failure=False):
    candles = list(mss_consecutive())
    candles[14] = bar(14, 74, opening=85 if overlap else 75, high=85 if overlap else 75, low=70)
    candles[15] = bar(15, 117, opening=100, high=120, low=100)
    candles[16] = bar(16, 95, opening=80, high=96, low=80)
    candles[17] = bar(17, 95, opening=90, high=100, low=90)
    candles[18] = bar(
        18, 66 if first_failure else 74, opening=90, high=90, low=65 if first_failure else 70
    )
    if overlap:
        candles[19] = bar(19, 85, opening=90, high=100, low=80)
    if first_failure:
        candles[22] = bar(22, 60, opening=105, high=106, low=59)
    return tuple(candles)


def events(frames):
    return tuple(e for frame in frames for e in frame.events)


def evaluations(frames):
    return tuple(e for frame in frames for e in frame.evidence)
