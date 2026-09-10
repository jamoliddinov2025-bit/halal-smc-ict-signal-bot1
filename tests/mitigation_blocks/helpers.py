"""Synthetic observations for first-interaction mitigation, never market forecasts."""

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.breaker_blocks import analyze_breaker_blocks
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.fvg import FVGConfig, analyze_fvg
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from smcsignal.analysis.mitigation_blocks import (
    MitigationBlockAnalyzer,
    MitigationBlockConfig,
    analyze_mitigation_blocks,
)
from smcsignal.analysis.mss import MSSConfig, analyze_mss
from smcsignal.analysis.order_blocks import (
    OrderBlockConfig,
    StructureRequirement,
    analyze_order_blocks,
)
from smcsignal.analysis.premium_discount import PDConfig, analyze_pd
from tests.analysis.helpers import bar
from tests.order_blocks.helpers import bullish as bullish_ob
from tests.order_blocks.helpers import choch

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase11-fixture:v1:from-first:missing=error"
)
ANALYSIS = AnalysisConfig(3)
LIQUIDITY = LiquidityConfig("USDT")
DISPLACEMENT = DisplacementConfig(atr_period=3)
FVG = FVGConfig()
OB = OrderBlockConfig()
PD = PDConfig()
MSS = MSSConfig()
DISPLACEMENT_ONLY = OrderBlockConfig(structure_requirement=StructureRequirement.DISPLACEMENT_ONLY)


def breaker_frames(
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
    return analyze_breaker_blocks(analyze_mss(analyze_pd(c, pd), MSS))


def run(candles, **kwargs):
    return analyze_mitigation_blocks(breaker_frames(candles, **kwargs))


def analyzer():
    return MitigationBlockAnalyzer(MitigationBlockConfig())


def bounce(index, low=14, close=16, opening=20, high=21):
    """Return into a bullish [13,15] zone without a far-boundary close-through."""
    return bar(index, close, opening=opening, high=high, low=low)


def bullish_mitigation():
    return (*bullish_ob()[:7], bounce(7))


def repeated():
    return (*bullish_ob()[:7], bounce(7), bounce(8, opening=16, high=17, low=14, close=16))


def before_breaker():
    return (
        *bullish_ob()[:7],
        bounce(7),
        bar(8, 19, opening=18, high=21, low=18),
        bar(9, 9, opening=19, high=20, low=8),
    )


def post_breaker():
    return (*choch(), bar(9, 14, opening=9, high=16, low=9))


def nested():
    return (
        *bullish_ob()[:7],
        bar(7, 20, opening=20, high=21, low=19),
        bar(8, 14, opening=18, high=18, low=12),
        bar(9, 25, opening=14, high=26, low=14),
        bar(10, 16, opening=25, high=25, low="13.5"),
    )


def golden():
    initial = tuple(bar(i, 10, opening=10, high=11, low=9) for i in range(14))
    source = bullish_ob()[:7]
    middle = tuple(
        bar(i + 14, c.close, opening=c.open, high=c.high, low=c.low) for i, c in enumerate(source)
    )
    return (
        *initial,
        *middle,
        bar(21, 16, opening=20, high=21, low=14),
        bar(22, 22, opening=16, high=23, low=16),
        bar(23, 9, opening=22, high=23, low=8),
        bar(24, 18, opening=9, high=20, low=9),
    )


def events(frames):
    return tuple(e for frame in frames for e in frame.events)
