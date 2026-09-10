"""Strict monthly-review-v1 invariants; descriptive text only, never advice."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "monthly-review-v1"

DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON = 10
MAXIMUM_FINALIZED_FOR_COMPARISON = 1000


@dataclass(frozen=True, slots=True)
class ReviewConfig:
    """Declare frozen monthly-review invariants.

    A monthly review recomputes nothing: it reads one Phase 19c performance
    report and renders descriptive text per UTC calendar month. Month-over-month
    deltas are quoted only when both months reach the configured minimum
    finalized count; sample sizes are always shown. There is no advice,
    forecast, optimization, or execution here.
    """

    enabled: bool = True
    minimum_finalized_for_comparison: int = DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict monthly-review-v1")
        if (
            type(self.minimum_finalized_for_comparison) is not int
            or not 1 <= self.minimum_finalized_for_comparison <= MAXIMUM_FINALIZED_FOR_COMPARISON
        ):
            raise AnalysisConfigurationError(
                "minimum_finalized_for_comparison must be an integer from 1 to "
                f"{MAXIMUM_FINALIZED_FOR_COMPARISON}"
            )


def load_review_config(path: str | Path) -> ReviewConfig:
    """Load the exact approved [review] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("review")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(
            f"cannot load monthly review configuration: {exc}"
        ) from exc
    names = {field.name for field in fields(ReviewConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[review] must contain exactly: " + ", ".join(sorted(names))
        )
    return ReviewConfig(**table)
