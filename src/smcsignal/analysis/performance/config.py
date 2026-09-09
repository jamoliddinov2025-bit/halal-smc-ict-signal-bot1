"""Strict performance-v1 invariants; descriptive statistics only, never advice."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "performance-v1"

DEFAULT_MINIMUM_FINALIZED_FOR_RANKING = 10
MAXIMUM_FINALIZED_FOR_RANKING = 1000


@dataclass(frozen=True, slots=True)
class PerformanceConfig:
    """Declare frozen descriptive-reporting invariants.

    Performance analytics recompute exact statistics over already-published
    Phase 18 outcome records and Phase 19b attribution profiles. There is no
    reclassification, no optimization, no expected-return forecast, and no
    execution. The only setting is how many finalized outcomes a group needs
    before best/worst rankings quote it.
    """

    enabled: bool = True
    minimum_finalized_for_ranking: int = DEFAULT_MINIMUM_FINALIZED_FOR_RANKING

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict performance-v1")
        if (
            type(self.minimum_finalized_for_ranking) is not int
            or not 1 <= self.minimum_finalized_for_ranking <= MAXIMUM_FINALIZED_FOR_RANKING
        ):
            raise AnalysisConfigurationError(
                "minimum_finalized_for_ranking must be an integer from 1 to "
                f"{MAXIMUM_FINALIZED_FOR_RANKING}"
            )


def load_performance_config(path: str | Path) -> PerformanceConfig:
    """Load the exact approved [performance] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("performance")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load performance configuration: {exc}") from exc
    names = {field.name for field in fields(PerformanceConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[performance] must contain exactly: " + ", ".join(sorted(names))
        )
    return PerformanceConfig(**table)
