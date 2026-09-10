"""Explicit price units and fixed-anchor equality tolerance, not quality settings."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Decimal, DecimalException, localcontext
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError


@dataclass(frozen=True, slots=True)
class LiquidityConfig:
    price_unit: str
    equal_tolerance_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if (
            not isinstance(self.price_unit, str)
            or not self.price_unit.strip()
            or self.price_unit != self.price_unit.strip()
        ):
            raise AnalysisConfigurationError("price_unit must be a nonempty, trimmed string")
        value = self.equal_tolerance_bps
        if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1000:
            raise AnalysisConfigurationError("equal_tolerance_bps must be a Decimal from 0 to 1000")
        # Count meaningful fractional digits without context-sensitive normalize().
        _, digits, exponent = value.as_tuple()
        if value:
            assert isinstance(exponent, int)
            trailing = 0
            for digit in reversed(digits):
                if digit:
                    break
                trailing += 1
            if exponent + trailing < -8:
                raise AnalysisConfigurationError("equal_tolerance_bps permits at most 8 decimals")


def price_band(price: Decimal, config: LiquidityConfig) -> tuple[Decimal, Decimal]:
    """Exact Decimal anchor +/- tolerance; no dependence on ambient precision."""
    if not config.equal_tolerance_bps:
        return price, price
    try:
        with localcontext() as context:
            context.prec = len(price.as_tuple().digits) + 40
            context.Emax, context.Emin, context.clamp = MAX_EMAX, MIN_EMIN, 0
            ratio = config.equal_tolerance_bps / Decimal(10000)
            lower, upper = price * (1 - ratio), price * (1 + ratio)
            if not lower.is_finite() or not upper.is_finite() or lower <= 0:
                raise AnalysisInputError("price band exceeds supported Decimal range")
            return lower, upper
    except DecimalException as exc:
        raise AnalysisInputError("price band exceeds supported Decimal range") from exc


def load_liquidity_config(path: str | Path) -> LiquidityConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("liquidity")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load liquidity configuration: {exc}") from exc
    if not isinstance(table, dict) or set(table) != {"price_unit", "equal_tolerance_bps"}:
        raise AnalysisConfigurationError(
            "[liquidity] must contain exactly price_unit and equal_tolerance_bps"
        )
    raw = table["equal_tolerance_bps"]
    if not isinstance(raw, str):
        raise AnalysisConfigurationError("equal_tolerance_bps must be a quoted decimal string")
    try:
        tolerance = Decimal(raw)
    except DecimalException as exc:
        raise AnalysisConfigurationError("invalid equal_tolerance_bps decimal") from exc
    return LiquidityConfig(price_unit=table["price_unit"], equal_tolerance_bps=tolerance)
