"""Strict OTE-v1 retracement geometry; not a signal or entry policy."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from decimal import Decimal, DecimalException
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "ote-v1"


class BoundaryPolicy(StrEnum):
    INCLUSIVE = "inclusive"


class PriceBasis(StrEnum):
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class OTEConfig:
    lower_retracement: Decimal = Decimal("0.62")
    upper_retracement: Decimal = Decimal("0.79")
    boundary_policy: BoundaryPolicy = BoundaryPolicy.INCLUSIVE
    price_basis: PriceBasis = PriceBasis.CLOSE

    def __post_init__(self) -> None:
        for name in ("lower_retracement", "upper_retracement"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise AnalysisConfigurationError(f"{name} must be a finite Decimal")
        lower, upper = self.lower_retracement, self.upper_retracement
        if not Decimal(0) < lower < upper < Decimal(1):
            raise AnalysisConfigurationError(
                "retracements must satisfy 0 < lower_retracement < upper_retracement < 1"
            )
        if not isinstance(self.boundary_policy, BoundaryPolicy):
            raise AnalysisConfigurationError("boundary_policy must be inclusive")
        if not isinstance(self.price_basis, PriceBasis):
            raise AnalysisConfigurationError("price_basis must be close")


def _decimal(raw: object, name: str) -> Decimal:
    if not isinstance(raw, str):
        raise AnalysisConfigurationError(f"{name} must be a quoted decimal string")
    try:
        return Decimal(raw)
    except DecimalException as exc:
        raise AnalysisConfigurationError(f"invalid {name} decimal") from exc


def load_ote_config(path: str | Path) -> OTEConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("ote")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load OTE configuration: {exc}") from exc
    names = {f.name for f in fields(OTEConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError("[ote] must contain exactly: " + ", ".join(sorted(names)))
    values = dict(table)
    values["lower_retracement"] = _decimal(values["lower_retracement"], "lower_retracement")
    values["upper_retracement"] = _decimal(values["upper_retracement"], "upper_retracement")
    try:
        values["boundary_policy"] = BoundaryPolicy(values["boundary_policy"])
    except (ValueError, TypeError) as exc:
        raise AnalysisConfigurationError("unsupported boundary_policy") from exc
    try:
        values["price_basis"] = PriceBasis(values["price_basis"])
    except (ValueError, TypeError) as exc:
        raise AnalysisConfigurationError("unsupported price_basis") from exc
    return OTEConfig(**values)
