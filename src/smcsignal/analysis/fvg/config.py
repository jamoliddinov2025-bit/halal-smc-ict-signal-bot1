"""Exact absolute-price gap threshold and optional aligned displacement filter."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "fvg-v1"


@dataclass(frozen=True, slots=True)
class FVGConfig:
    min_gap_size: Decimal = Decimal("0")
    require_displacement: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.min_gap_size, Decimal)
            or not self.min_gap_size.is_finite()
            or self.min_gap_size < 0
        ):
            raise AnalysisConfigurationError(
                "min_gap_size must be a finite nonnegative Decimal in the upstream price unit"
            )
        if type(self.require_displacement) is not bool:
            raise AnalysisConfigurationError("require_displacement must be a boolean")


def load_fvg_config(path: str | Path) -> FVGConfig:
    """Require both [fvg] keys; price distances must be quoted decimal strings."""
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("fvg")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load FVG configuration: {exc}") from exc
    if not isinstance(table, dict) or set(table) != {"min_gap_size", "require_displacement"}:
        raise AnalysisConfigurationError(
            "[fvg] must contain exactly min_gap_size and require_displacement"
        )
    if not isinstance(table["min_gap_size"], str):
        raise AnalysisConfigurationError("min_gap_size must be a quoted decimal string")
    try:
        minimum = Decimal(table["min_gap_size"])
    except DecimalException as exc:
        raise AnalysisConfigurationError("invalid min_gap_size decimal") from exc
    return FVGConfig(minimum, table["require_displacement"])
