"""Reuse existing displacement fixtures; indicators do not rerun earlier detectors."""

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from smcsignal.analysis import AnalysisConfig, SeriesProvenance
from smcsignal.analysis.displacement import DisplacementConfig, analyze_displacement
from smcsignal.analysis.indicators import (
    IndicatorAnalyzer,
    IndicatorsConfig,
    analyze_indicators,
)
from smcsignal.analysis.liquidity import LiquidityConfig, analyze_liquidity
from tests.mtf.helpers import EIGHT, bars

RISING = tuple(range(20, 37))
SERIES = SeriesProvenance(
    "BTCUSDT", "15m", "synthetic_spot", "csv", "phase19-fixture:v1:from-first:missing=error"
)
ANALYSIS = AnalysisConfig(3)
LIQUIDITY = LiquidityConfig("USDT")
DISPLACEMENT = DisplacementConfig(atr_period=3)

SMALL = IndicatorsConfig(ema_periods=(3, 5), rsi_period=3, volume_average_period=4)


def candles_for(prices=RISING, *, volumes=None):
    if volumes is None:
        return bars("15m", prices, start=EIGHT)
    step = 15 * 60
    return tuple(
        _bar_at_seconds(EIGHT, index * step, price, volume)
        for index, (price, volume) in enumerate(zip(prices, volumes, strict=True))
    )


def _bar_at_seconds(base, offset, close, volume):
    from datetime import timedelta

    from tests.mtf.helpers import bar_at

    return bar_at(base + timedelta(seconds=offset), close, volume=volume)


def displacement_frames(candles=None):
    a = analyze_liquidity(
        candles if candles is not None else candles_for(),
        series=SERIES,
        config=LIQUIDITY,
        analysis_config=ANALYSIS,
    )
    return analyze_displacement(a, DISPLACEMENT, price_unit=LIQUIDITY.price_unit)


def run(frames=None, config=None, *, candles=None):
    return analyze_indicators(
        frames if frames is not None else displacement_frames(candles),
        config if config is not None else SMALL,
    )


def analyzer(config=None):
    return IndicatorAnalyzer(config if config is not None else SMALL)


def reference(value: Decimal) -> Decimal:
    """Round-trip check helper: values are already 50-digit half-even results."""

    return value


def ratio_like(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Independent 50-significant-digit half-even reference computation."""

    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return numerator / denominator


def at50(computation):
    """Run a reference expression under the same 50-digit half-even context."""

    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return computation()


__all__ = [
    "RISING",
    "at50",
    "SMALL",
    "analyzer",
    "candles_for",
    "displacement_frames",
    "ratio_like",
    "reference",
    "run",
]
