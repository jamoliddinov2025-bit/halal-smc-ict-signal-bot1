"""Geometric equilibrium tolerance only; no signals, ranking or execution policy."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "premium-discount-v1"
RANGE_VERSION = "latest-confirmed-opposing-pair-v1"


@dataclass(frozen=True, slots=True)
class PDConfig:
    equilibrium_half_width_fraction: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        value = self.equilibrium_half_width_fraction
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not 0 <= value <= Decimal("0.5")
        ):
            raise AnalysisConfigurationError(
                "equilibrium_half_width_fraction must be a finite Decimal from 0 to 0.5"
            )


def load_pd_config(path: str | Path) -> PDConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("premium_discount")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load PD configuration: {exc}") from exc
    if not isinstance(table, dict) or set(table) != {"equilibrium_half_width_fraction"}:
        raise AnalysisConfigurationError(
            "[premium_discount] must contain exactly equilibrium_half_width_fraction"
        )
    raw = table["equilibrium_half_width_fraction"]
    if not isinstance(raw, str):
        raise AnalysisConfigurationError(
            "equilibrium_half_width_fraction must be a quoted decimal string"
        )
    try:
        value = Decimal(raw)
    except DecimalException as exc:
        raise AnalysisConfigurationError("invalid equilibrium half-width decimal") from exc
    return PDConfig(value)
