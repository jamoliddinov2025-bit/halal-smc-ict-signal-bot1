"""Strict outcome-tracking-v1 invariants; not execution, entries, exits, or fees."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "outcome-tracking-v1"

DEFAULT_HORIZON_BARS = 10
MAX_HORIZON_BARS = 10_000


@dataclass(frozen=True, slots=True)
class OutcomeTrackingConfig:
    """Declare frozen BUY_SIGNAL outcome invariants.

    The evaluation horizon is the only setting. Reference price, WIN/LOSS/FLAT
    rules, and BUY-only scope are fixed methodology, not knobs. No entry, exit,
    stop, target, fee, slippage, sizing, sell, or optimization setting exists.
    """

    enabled: bool = True
    horizon_bars: int = DEFAULT_HORIZON_BARS

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict outcome-tracking-v1")
        if type(self.horizon_bars) is not int or not 1 <= self.horizon_bars <= MAX_HORIZON_BARS:
            raise AnalysisConfigurationError(
                f"horizon_bars must be an integer from 1 to {MAX_HORIZON_BARS}"
            )


def load_outcome_tracking_config(path: str | Path) -> OutcomeTrackingConfig:
    """Load the exact approved [outcome_tracking] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("outcome_tracking")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(
            f"cannot load outcome tracking configuration: {exc}"
        ) from exc
    names = {field.name for field in fields(OutcomeTrackingConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[outcome_tracking] must contain exactly: " + ", ".join(sorted(names))
        )
    return OutcomeTrackingConfig(**table)
