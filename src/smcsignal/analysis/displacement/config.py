"""Objective displacement thresholds, not scores, probabilities, or signals."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from decimal import Decimal, DecimalException
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "displacement-v1"
ATR_METHODOLOGY_VERSION = "atr-sma-v1"


@dataclass(frozen=True, slots=True)
class DisplacementConfig:
    atr_period: int = 14
    min_body_atr: Decimal = Decimal("1.0")
    min_range_atr: Decimal = Decimal("1.5")
    bullish_close_min: Decimal = Decimal("0.70")
    bearish_close_max: Decimal = Decimal("0.30")
    atr_floor: Decimal = Decimal("0")
    sweep_lookback_bars: int = 20

    def __post_init__(self) -> None:
        if type(self.atr_period) is not int or not 1 <= self.atr_period <= 1000:
            raise AnalysisConfigurationError("atr_period must be an integer from 1 to 1000")
        if type(self.sweep_lookback_bars) is not int or not 0 <= self.sweep_lookback_bars <= 10000:
            raise AnalysisConfigurationError(
                "sweep_lookback_bars must be an integer from 0 to 10000"
            )
        for name in (
            "min_body_atr",
            "min_range_atr",
            "bullish_close_min",
            "bearish_close_max",
            "atr_floor",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise AnalysisConfigurationError(f"{name} must be a finite Decimal")
        if not 0 < self.min_body_atr <= 1000 or not 0 < self.min_range_atr <= 1000:
            raise AnalysisConfigurationError(
                "ATR multipliers must be greater than 0 and at most 1000"
            )
        if not 0 <= self.bearish_close_max <= self.bullish_close_min <= 1:
            raise AnalysisConfigurationError(
                "close thresholds require 0 <= bearish <= bullish <= 1"
            )
        if self.atr_floor < 0:
            raise AnalysisConfigurationError(
                "atr_floor must be nonnegative, in the declared price unit"
            )


def load_displacement_config(path: str | Path) -> DisplacementConfig:
    """Require all seven explicit keys; decimal quantities are quoted strings."""
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("displacement")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load displacement configuration: {exc}") from exc
    required = {field.name for field in fields(DisplacementConfig)}
    if not isinstance(table, dict) or set(table) != required:
        raise AnalysisConfigurationError(
            "[displacement] must contain exactly: " + ", ".join(sorted(required))
        )
    values = dict(table)
    for name in sorted(required - {"atr_period", "sweep_lookback_bars"}):
        if not isinstance(values[name], str):
            raise AnalysisConfigurationError(f"{name} must be a quoted decimal string")
        try:
            values[name] = Decimal(values[name])
        except DecimalException as exc:
            raise AnalysisConfigurationError(f"invalid decimal for {name}") from exc
    return DisplacementConfig(**values)
