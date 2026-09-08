"""Synthetic OTE fixtures through the unchanged upstream PD pipeline."""

from decimal import Decimal

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.fvg import FVGConfig, analyze_fvg
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from smcsignal.analysis.order_blocks import OrderBlockConfig, analyze_order_blocks
from smcsignal.analysis.ote import OTEAnalyzer, OTEConfig, analyze_ote
from smcsignal.analysis.premium_discount import PDConfig, analyze_pd
from tests.analysis.helpers import bar
from tests.premium_discount.helpers import golden as pd_golden

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase12-fixture:v1:from-first:missing=error"
)
ANALYSIS = AnalysisConfig(3)
LIQUIDITY = LiquidityConfig("USDT")
DISPLACEMENT = DisplacementConfig(atr_period=3)
FVG = FVGConfig()
OB = OrderBlockConfig()
PD = PDConfig()


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
    return analyze_pd(analyze_order_blocks(analyze_fvg(b, fvg), order_blocks), pd)


def run(candles, config=None, **kwargs):
    return analyze_ote(upstream(candles, **kwargs), config if config is not None else OTEConfig())


def analyzer(config=None):
    return OTEAnalyzer(config if config is not None else OTEConfig())


def golden():
    """Bullish [9,41] published at 4; later equal-extrema candles keep that range."""
    return (
        bar(0, 20),
        bar(1, 10),
        bar(2, 20),
        bar(3, 40),
        bar(4, 25),
        bar(5, 18, high=30, low=10),
        bar(6, 12, high=30, low=10),
        bar(7, 25, high=30, low=10),
        bar(8, "15.72", high=30, low=10),
        bar(9, "21.16", high=30, low=10),
    )


def bearish_stable():
    """Keep the first PD golden bearish range [11,16] by not confirming a new high."""
    return (*pd_golden()[:4], bar(4, "14.5", high=16, low=12))


def classified(close, *, high=30, low=10):
    return (*golden()[:5], bar(5, close, high=high, low=low))


BULLISH_LOW = Decimal(9)
BULLISH_HIGH = Decimal(41)
BULLISH_OTE_LOWER = Decimal("15.72")
BULLISH_OTE_UPPER = Decimal("21.16")
BEARISH_OTE_LOWER = Decimal("14.1")
BEARISH_OTE_UPPER = Decimal("14.95")
