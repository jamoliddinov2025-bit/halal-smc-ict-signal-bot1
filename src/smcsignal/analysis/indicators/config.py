"""Strict indicators-v1 invariants; context only, never signals or gates."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "indicators-v1"

MAX_PERIOD = 1000


def _periods(value: object, name: str) -> tuple[int, ...]:
    if not isinstance(value, tuple) or not value:
        raise AnalysisConfigurationError(f"{name} must be a nonempty tuple of periods")
    for item in value:
        if type(item) is not int or not 1 <= item <= MAX_PERIOD:
            raise AnalysisConfigurationError(
                f"{name} entries must be integers from 1 to {MAX_PERIOD}"
            )
    if any(later <= earlier for earlier, later in zip(value, value[1:], strict=False)):
        raise AnalysisConfigurationError(f"{name} must be strictly increasing")
    return value


def _period(value: object, name: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_PERIOD:
        raise AnalysisConfigurationError(f"{name} must be an integer from 1 to {MAX_PERIOD}")
    return value


@dataclass(frozen=True, slots=True)
class IndicatorsConfig:
    """Declare frozen supporting-indicator invariants.

    Indicators are context and visualization overlays only. There is no
    threshold, gate, signal, veto, or execution setting; any such key is
    rejected by the loader. ATR is reused from Phase 5, never recomputed.
    """

    enabled: bool = True
    ema_periods: tuple[int, ...] = (20, 50)
    rsi_period: int = 14
    volume_average_period: int = 20

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict indicators-v1")
        object.__setattr__(self, "ema_periods", _periods(self.ema_periods, "ema_periods"))
        _period(self.rsi_period, "rsi_period")
        _period(self.volume_average_period, "volume_average_period")


def load_indicators_config(path: str | Path) -> IndicatorsConfig:
    """Load the exact approved [indicators] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("indicators")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load indicators configuration: {exc}") from exc
    names = {field.name for field in fields(IndicatorsConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[indicators] must contain exactly: " + ", ".join(sorted(names))
        )
    values = dict(table)
    if isinstance(values.get("ema_periods"), list):
        values["ema_periods"] = tuple(values["ema_periods"])
    return IndicatorsConfig(**values)
