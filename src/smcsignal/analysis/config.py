"""Explicit, immutable configuration for confirmed fractal analysis."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Total odd fractal window length, not the number of bars on one side."""

    fractal_length: int = 5

    def __post_init__(self) -> None:
        if (
            type(self.fractal_length) is not int
            or not 3 <= self.fractal_length <= 1001
            or self.fractal_length % 2 == 0
        ):
            raise AnalysisConfigurationError("fractal_length must be an odd integer from 3 to 1001")

    @property
    def confirmation_delay(self) -> int:
        """Number of closed candles required on each side of the pivot."""
        return (self.fractal_length - 1) // 2


def load_analysis_config(path: str | Path) -> AnalysisConfig:
    """Load only [analysis], rejecting missing keys, typos, and unknown options."""
    try:
        with Path(path).open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load analysis configuration: {exc}") from exc
    table = document.get("analysis")
    if not isinstance(table, dict) or set(table) != {"fractal_length"}:
        raise AnalysisConfigurationError("[analysis] must contain exactly fractal_length")
    return AnalysisConfig(fractal_length=table["fractal_length"])
