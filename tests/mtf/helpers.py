"""Synthetic multi-timeframe OTE fixtures; prices are not aggregated across TFs."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smcsignal.analysis import SeriesProvenance
from smcsignal.analysis.mtf import MTFAnalyzer, MTFConfig, analyze_mtf, timeframe_seconds
from smcsignal.analysis.ote import analyze_ote
from smcsignal.data import OHLCV
from tests.ote.helpers import upstream

MIDNIGHT = datetime(2024, 1, 1, tzinfo=UTC)
EIGHT = datetime(2024, 1, 1, 8, tzinfo=UTC)
BULLISH_HTF = (15, 10, 16, 20, 18, 12, 17, 22, 19)
BEARISH_HTF = (15, 20, 14, 10, 12, 18, 13, 8, 11)
HOUR_PRICES = (*BULLISH_HTF, 18, 17, 16)
PRIMARY_PRICES = tuple(range(20, 37))
FOUR_HOUR_PRICES = (15,)


def series_for(timeframe, dataset="phase13-fixture:v1"):
    return SeriesProvenance("BTCUSDT", timeframe, "synthetic_spot", "csv", dataset)


def bar_at(opened_at, close, *, high=None, low=None, opening=None, volume=1):
    close = Decimal(str(close))
    return OHLCV(
        timestamp=opened_at,
        open=close if opening is None else Decimal(str(opening)),
        high=close + 1 if high is None else Decimal(str(high)),
        low=close - 1 if low is None else Decimal(str(low)),
        close=close,
        volume=Decimal(str(volume)),
    )


def bars(timeframe, prices, *, start=MIDNIGHT):
    step = timedelta(seconds=timeframe_seconds(timeframe))
    return tuple(bar_at(start + index * step, price) for index, price in enumerate(prices))


def ote_frames(candles, timeframe, *, dataset="phase13-fixture:v1", **kwargs):
    series = kwargs.pop("series", None) or series_for(timeframe, dataset)
    return analyze_ote(upstream(candles, series=series, **kwargs))


def primary_15m():
    return ote_frames(bars("15m", PRIMARY_PRICES, start=EIGHT), "15m")


def hour_frames():
    return ote_frames(bars("1h", HOUR_PRICES, start=MIDNIGHT), "1h")


def four_hour_frames():
    return ote_frames(bars("4h", FOUR_HOUR_PRICES, start=EIGHT), "4h")


def higher():
    return {"1h": hour_frames(), "4h": four_hour_frames()}


def run(primary=None, frames=None, config=None):
    return analyze_mtf(
        primary if primary is not None else primary_15m(),
        frames if frames is not None else higher(),
        config if config is not None else MTFConfig(),
    )


def analyzer(config=None, frames=None):
    return MTFAnalyzer(config if config is not None else MTFConfig(), higher=frames or higher())
