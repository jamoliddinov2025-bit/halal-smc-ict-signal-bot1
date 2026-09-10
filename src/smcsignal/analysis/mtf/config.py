"""Strict MTF-v1 join settings; not a score, signal, or resampling policy."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.mtf.timeframes import require_higher_multiple, timeframe_seconds

METHODOLOGY_VERSION = "mtf-v1"


class AvailabilityPolicy(StrEnum):
    COMPLETED_CANDLE = "completed_candle"


@dataclass(frozen=True, slots=True)
class MTFConfig:
    enabled: bool = True
    primary_timeframe: str = "15m"
    higher_timeframes: tuple[str, ...] = ("1h", "4h")
    availability_policy: AvailabilityPolicy = AvailabilityPolicy.COMPLETED_CANDLE

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict mtf-v1")
        timeframe_seconds(self.primary_timeframe)
        if not isinstance(self.higher_timeframes, tuple) or not self.higher_timeframes:
            raise AnalysisConfigurationError(
                "higher_timeframes must be a nonempty tuple of timeframe strings"
            )
        seen: set[str] = set()
        for timeframe in self.higher_timeframes:
            if not isinstance(timeframe, str):
                raise AnalysisConfigurationError("higher_timeframes entries must be strings")
            if timeframe == self.primary_timeframe:
                raise AnalysisConfigurationError(
                    "primary_timeframe cannot also be a higher timeframe"
                )
            if timeframe in seen:
                raise AnalysisConfigurationError("higher_timeframes cannot contain duplicates")
            require_higher_multiple(self.primary_timeframe, timeframe)
            seen.add(timeframe)
        if not isinstance(self.availability_policy, AvailabilityPolicy):
            raise AnalysisConfigurationError("availability_policy must be completed_candle")


def load_mtf_config(path: str | Path) -> MTFConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("mtf")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load MTF configuration: {exc}") from exc
    names = {field.name for field in fields(MTFConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError("[mtf] must contain exactly: " + ", ".join(sorted(names)))
    values = dict(table)
    raw_higher = values.get("higher_timeframes")
    if (
        not isinstance(raw_higher, list)
        or not raw_higher
        or not all(isinstance(item, str) for item in raw_higher)
    ):
        raise AnalysisConfigurationError("higher_timeframes must be an array of timeframe strings")
    values["higher_timeframes"] = tuple(raw_higher)
    try:
        values["availability_policy"] = AvailabilityPolicy(values["availability_policy"])
    except (ValueError, TypeError) as exc:
        raise AnalysisConfigurationError("unsupported availability_policy") from exc
    return MTFConfig(**values)
