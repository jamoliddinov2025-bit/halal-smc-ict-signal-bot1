"""Strict improvement-v1 research-framework invariants; never a strategy change.

Phase 23 is a controlled, offline, evidence-driven self-improvement research
framework over the frozen Phase 22 signal system. This configuration tunes
only the research harness: the sample minimum used before a candidate-versus-
baseline comparison may carry an improvement claim, and the mandatory
human-approval gate. It contains no strategy parameter, cannot alter signal
logic, thresholds, weights, setup enablement, eligibility, or halal status,
and never promotes anything into production.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "improvement-v1"

DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON = 30


@dataclass(frozen=True, slots=True)
class ImprovementConfig:
    """Declare frozen Phase 23 research-framework invariants.

    ``minimum_finalized_for_comparison`` is the smallest finalized sample for
    which a candidate-versus-baseline comparison may carry an improvement
    claim; below it a comparison is reported inconclusive and never treated as
    proof. ``human_approval_required`` must stay true: no Phase 23 component
    may approve or reject a candidate, and nothing here can promote a candidate
    into production. ``enabled`` must be true in strict improvement-v1.
    """

    enabled: bool = True
    minimum_finalized_for_comparison: int = DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON
    human_approval_required: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict improvement-v1")
        minimum = self.minimum_finalized_for_comparison
        if type(minimum) is not int or minimum < 1:
            raise AnalysisConfigurationError(
                "minimum_finalized_for_comparison must be an integer from 1 upward"
            )
        if type(self.human_approval_required) is not bool or not self.human_approval_required:
            raise AnalysisConfigurationError(
                "human_approval_required must be true in strict improvement-v1"
            )


def _scalar(value: object) -> bool:
    return isinstance(value, (bool, int, str))


def load_improvement_config(path: str | Path) -> ImprovementConfig:
    """Load the exact approved [improvement] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("improvement")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load improvement configuration: {exc}") from exc
    names = {field.name for field in fields(ImprovementConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[improvement] must contain exactly: " + ", ".join(sorted(names))
        )
    for key in table:
        if not _scalar(table[key]):
            raise AnalysisConfigurationError(f"{key} must be a boolean, integer, or string")
    # Construction applies the strict type/value invariants in __post_init__.
    return ImprovementConfig(**table)
