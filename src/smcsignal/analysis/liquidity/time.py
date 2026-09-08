"""Closed-bar boundaries from declared UTC intervals, never the next data row."""

from datetime import datetime, timedelta

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.provenance import _instant
from smcsignal.data.config import SUPPORTED_TIMEFRAMES


def candle_close_time(opened_at: datetime, timeframe: str) -> datetime:
    opened_at = _instant(opened_at, "opened_at")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise AnalysisInputError("unsupported liquidity timeframe")
    try:
        if timeframe == "1M":
            if (
                opened_at.day,
                opened_at.hour,
                opened_at.minute,
                opened_at.second,
                opened_at.microsecond,
            ) != (1, 0, 0, 0, 0):
                raise AnalysisInputError(
                    "monthly candles must open at a UTC calendar-month boundary"
                )
            return opened_at.replace(
                year=opened_at.year + (opened_at.month == 12),
                month=1 if opened_at.month == 12 else opened_at.month + 1,
            )
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        return opened_at + timedelta(seconds=int(timeframe[:-1]) * seconds[timeframe[-1]])
    except AnalysisInputError:
        raise
    except (ValueError, OverflowError) as exc:
        raise AnalysisInputError("candle closure is outside the supported datetime range") from exc
