"""Synthetic, hand-audited range/PD fixtures through the unchanged upstream pipeline."""

from decimal import Decimal

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.fvg import FVGConfig, analyze_fvg
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from smcsignal.analysis.order_blocks import OrderBlockConfig, analyze_order_blocks
from smcsignal.analysis.premium_discount import PDAnalyzer, PDConfig, analyze_pd
from tests.analysis.helpers import bar

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase8-fixture:v1:from-first:missing=error"
)
ANALYSIS = AnalysisConfig(3)
LIQUIDITY = LiquidityConfig("USDT")
DISPLACEMENT = DisplacementConfig(atr_period=3)
FVG = FVGConfig()
OB = OrderBlockConfig()


def upstream(
    candles,
    *,
    series=SERIES,
    analysis=ANALYSIS,
    liquidity=LIQUIDITY,
    displacement=DISPLACEMENT,
    fvg=FVG,
    order_blocks=OB,
):
    a = analyze_liquidity(candles, series=series, config=liquidity, analysis_config=analysis)
    b = analyze_displacement(a, displacement, price_unit=liquidity.price_unit)
    return analyze_order_blocks(analyze_fvg(b, fvg), order_blocks)


def run(candles, fraction="0", **kwargs):
    return analyze_pd(upstream(candles, **kwargs), PDConfig(Decimal(fraction)))


def analyzer(fraction="0"):
    return PDAnalyzer(PDConfig(Decimal(fraction)))


def golden():
    return tuple(bar(i, price) for i, price in enumerate([10, 15, 12, 15, 12, "13.5", 10]))
