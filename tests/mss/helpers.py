"""Synthetic closed-candle observations; no trade or performance simulation."""

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.fvg import FVGConfig, analyze_fvg
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from smcsignal.analysis.mss import MSSAnalyzer, MSSConfig, analyze_mss
from smcsignal.analysis.order_blocks import OrderBlockConfig, analyze_order_blocks
from smcsignal.analysis.premium_discount import PDConfig, analyze_pd
from tests.analysis.helpers import bar

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase9-fixture:v1:from-first:missing=error"
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


def run(candles, **kwargs):
    return analyze_mss(upstream(candles, **kwargs))


def analyzer():
    return MSSAnalyzer(MSSConfig())


def base():
    rows = [
        (10, 11, 9, 10),
        (15, 16, 14, 15),
        (12, 13, 11, 12),
        (18, 19, 17, 18),
        (14, 15, 13, 14),
        (16, 17, 15, 16),
        (20, 21, 19, 20),
        (16, 18, 16, 17),
        (16, 16, 10, 11),
        (16, 17, 15, 16),
        (10, 11, 9, 10),
        (14, 15, 13, 14),
        (9, 9, 7, 8),
        (16, 31, 16, 30),
    ]
    return tuple(bar(i, c, opening=o, high=h, low=low) for i, (o, h, low, c) in enumerate(rows))


def golden():
    initial = tuple(bar(i, 10, opening=10, high=11, low=9) for i in range(14))
    return (
        *initial,
        *(
            bar(i + 14, c.close, opening=c.open, high=c.high, low=c.low)
            for i, c in enumerate(base())
        ),
    )


def consecutive():
    initial = tuple(bar(i, 90, opening=90, high=91, low=89) for i in range(15))
    rows = [
        (110, 120, 100, 110),
        (90, 95, 80, 90),
        (95, 100, 90, 95),
        (80, 90, 70, 80),
        (85, 100, 80, 85),
        (90, 110, 75, 90),
        (80, 108, 80, 105),
        (105, 106, 65, 66),
    ]
    return (
        *initial,
        *(bar(i + 15, c, opening=o, high=h, low=low) for i, (o, h, low, c) in enumerate(rows)),
    )


def events(frames):
    return tuple(e for f in frames for e in f.events)
