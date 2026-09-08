"""Synthetic raw observations with hand-audited liquidity/sweep outcomes."""

from decimal import Decimal

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquidityConfig, analyze_liquidity
from tests.analysis.helpers import bar, mirrored

SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase4-fixture:start-2024-01-01"
)
CONFIG = LiquidityConfig("USDT")
ANALYSIS = AnalysisConfig(3)


def golden():
    rows = (
        (10, 11, 9, 10),
        (15, 16, 14, 15),
        (12, 13, 11, 12),
        (15, 16, 14, 15),
        (12, 13, 11, 12),
        (12, 13, 9, 10),
        (12, 13, 11, 12),
        (10, 13, 9, 10),
        (12, 13, 11, 12),
        (12, 17, 11, 12),
        (12, 13, 8, 12),
        (12, 13, 10, 12),
    )
    return tuple(bar(i, c, opening=o, high=h, low=low) for i, (o, h, low, c) in enumerate(rows))


def analyzer(*, bps="0", length=3, series=SERIES):
    return LiquidityAnalyzer(
        series=series,
        config=LiquidityConfig("USDT", Decimal(bps)),
        analysis_config=AnalysisConfig(length),
    )


def run(candles, *, bps="0", length=3, series=SERIES):
    return analyze_liquidity(
        candles,
        series=series,
        config=LiquidityConfig("USDT", Decimal(bps)),
        analysis_config=AnalysisConfig(length),
    )


def single_high(tail, *, mirror=False):
    candles = (bar(0, 10), bar(1, 15), bar(2, 12), tail)
    return mirrored(candles) if mirror else candles


def pool_updates(frames):
    return tuple(p for frame in frames for p in frame.pool_updates)


def sweeps(frames):
    return tuple(s for frame in frames for s in frame.sweeps)
