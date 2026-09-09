"""Strict intelligence-v1 invariants; research diagnostics, never optimization.

Phase 22 Strategy Intelligence is a consumer-only descriptive layer over the
validated Phase 21 walk-forward ``RobustnessReport``. It describes how the
published, out-of-sample spot BUY_SIGNAL facts (outcomes, attribution, causal
regime context) distribute across signal-time strategy profiles. Every
diagnostic is a deterministic label over descriptive statistics gated by a
minimum finalized sample.

Nothing in this layer selects, enables or disables a strategy, tunes a
parameter or threshold, vetoes a signal, re-detects a regime or setup,
modifies a detector or score, feeds results back into signal generation, or
presents trading advice. It is an observational research record only. The
boundary with the not-approved Phase 23 optimization layer is explicit: this
configuration performs no optimization whatsoever.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "intelligence-v1"

DEFAULT_MINIMUM_FINALIZED_FOR_DIAGNOSIS = 20
DEFAULT_MINIMUM_FINALIZED_FOR_RANKING = 20
DEFAULT_WINNER_WIN_RATE_FLOOR = Decimal("0.60")
DEFAULT_LOSER_WIN_RATE_CEILING = Decimal("0.40")


@dataclass(frozen=True, slots=True)
class IntelligenceConfig:
    """Declare frozen intelligence-gating invariants.

    ``minimum_finalized_for_diagnosis`` is the smallest finalized sample for
    which a descriptive group receives a winner/loser pattern and a
    strength/weakness diagnostic; below it the group is reported undersampled
    with its raw counts kept visible. ``minimum_finalized_for_ranking`` is the
    smallest finalized sample for which a group may appear in a deterministic
    ranking list, and is required to be at least as large as the diagnosis
    minimum so any ranked group is also diagnosed.

    The pattern and diagnostic rules are deterministic and purely descriptive:
    a sufficient group is a WINNER when its win rate reaches the
    ``winner_win_rate_floor`` and its average final return is positive, a LOSER
    when its win rate is at most the ``loser_win_rate_ceiling`` and its average
    final return is negative, and NEUTRAL otherwise. Requiring the ceiling to
    sit strictly below the floor keeps the two patterns mutually exclusive.
    These labels summarize published records; they never drive a decision.
    """

    enabled: bool = True
    minimum_finalized_for_diagnosis: int = DEFAULT_MINIMUM_FINALIZED_FOR_DIAGNOSIS
    minimum_finalized_for_ranking: int = DEFAULT_MINIMUM_FINALIZED_FOR_RANKING
    winner_win_rate_floor: Decimal = DEFAULT_WINNER_WIN_RATE_FLOOR
    loser_win_rate_ceiling: Decimal = DEFAULT_LOSER_WIN_RATE_CEILING

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict intelligence-v1")
        for name, value in (
            ("minimum_finalized_for_diagnosis", self.minimum_finalized_for_diagnosis),
            ("minimum_finalized_for_ranking", self.minimum_finalized_for_ranking),
        ):
            if type(value) is not int or value < 1:
                raise AnalysisConfigurationError(f"{name} must be an integer from 1 upward")
        if self.minimum_finalized_for_ranking < self.minimum_finalized_for_diagnosis:
            raise AnalysisConfigurationError(
                "minimum_finalized_for_ranking must be at least the diagnosis minimum"
            )
        for threshold_name, threshold in (
            ("winner_win_rate_floor", self.winner_win_rate_floor),
            ("loser_win_rate_ceiling", self.loser_win_rate_ceiling),
        ):
            if not isinstance(threshold, Decimal) or not threshold.is_finite() or threshold <= 0:
                raise AnalysisConfigurationError(
                    f"{threshold_name} must be a positive, finite Decimal"
                )
        if self.winner_win_rate_floor > 1:
            raise AnalysisConfigurationError("winner_win_rate_floor cannot exceed one")
        if self.loser_win_rate_ceiling > 1:
            raise AnalysisConfigurationError("loser_win_rate_ceiling cannot exceed one")
        if self.loser_win_rate_ceiling >= self.winner_win_rate_floor:
            raise AnalysisConfigurationError(
                "loser_win_rate_ceiling must be below winner_win_rate_floor "
                "so the winner and loser patterns stay mutually exclusive"
            )


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise AnalysisConfigurationError(f"{name} must be a decimal string") from exc
    raise AnalysisConfigurationError(f"{name} must be a decimal string")


def load_intelligence_config(path: str | Path) -> IntelligenceConfig:
    """Load the exact approved [intelligence] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("intelligence")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load intelligence configuration: {exc}") from exc
    names = {field.name for field in fields(IntelligenceConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[intelligence] must contain exactly: " + ", ".join(sorted(names))
        )
    for key in table:
        if not isinstance(table[key], (bool, int, str)):
            raise AnalysisConfigurationError(f"{key} must be a boolean, integer, or decimal string")
    return IntelligenceConfig(
        enabled=table["enabled"],
        minimum_finalized_for_diagnosis=table["minimum_finalized_for_diagnosis"],
        minimum_finalized_for_ranking=table["minimum_finalized_for_ranking"],
        winner_win_rate_floor=_decimal(table["winner_win_rate_floor"], "winner_win_rate_floor"),
        loser_win_rate_ceiling=_decimal(table["loser_win_rate_ceiling"], "loser_win_rate_ceiling"),
    )
