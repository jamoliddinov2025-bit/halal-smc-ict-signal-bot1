"""Fixed-duration HTF relationships; never resample or invent calendar months."""

from __future__ import annotations

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.data.config import SUPPORTED_TIMEFRAMES

_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(timeframe: str) -> int:
    """Return the exact UTC duration of a supported fixed interval."""
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise AnalysisConfigurationError(
            f"unsupported timeframe; choose from {', '.join(SUPPORTED_TIMEFRAMES)}"
        )
    if timeframe == "1M":
        raise AnalysisConfigurationError(
            "1M is not a fixed-duration integer-multiple timeframe in mtf-v1"
        )
    return int(timeframe[:-1]) * _SECONDS[timeframe[-1]]


def require_higher_multiple(primary: str, higher: str) -> None:
    """Reject equal, shorter, or non-integer-multiple higher timeframes."""
    primary_seconds = timeframe_seconds(primary)
    higher_seconds = timeframe_seconds(higher)
    if higher_seconds <= primary_seconds or higher_seconds % primary_seconds:
        raise AnalysisConfigurationError(
            f"{higher} is not a strictly higher integer multiple of {primary}"
        )
