"""Controlled candidate creation from a human-confirmed hypothesis.

A candidate is only created from a hypothesis after an explicit human
confirmation (decision = CONFIRMED) exists. The candidate's explicit delta
still goes through the full Phase 23A CandidateDelta validation (allow-list,
baseline anchor, distinct valid new value), and the experiment must declare a
complete, offline research protocol (dataset identity, symbol/timeframe scope,
historical time range, walk-forward configuration, planned validation windows,
and a minimum finalized sample). Incomplete experiment definitions are
rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.evidence import check_baseline_hash, compose_id
from smcsignal.analysis.improvement.hypotheses import (
    ConfirmationDecision,
    HypothesisConfirmation,
)
from smcsignal.analysis.improvement.models import (
    CandidateDelta,
    CandidateExperimentDef,
    Hypothesis,
    build_candidate,
)
from smcsignal.analysis.robustness import RobustnessConfig


@dataclass(frozen=True, slots=True)
class ExperimentDeclaration:
    """The complete, explicit, offline research protocol of one candidate."""

    name: str
    dataset_ids: tuple[str, ...]
    symbol: str
    timeframe: str
    historical_start: datetime
    historical_end: datetime
    robustness: RobustnessConfig
    planned_validation_windows: int
    minimum_finalized_for_comparison: int

    def __post_init__(self) -> None:
        validate_declaration(self)

    def identity(self) -> str:
        """Deterministic declaration identity from its explicit frozen inputs."""
        return compose_id(
            "experiment",
            {
                "methodology": "improvement-v1",
                "name": self.name,
                "dataset_ids": sorted(self.dataset_ids),
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "historical_start": self.historical_start,
                "historical_end": self.historical_end,
                "robustness": self.robustness,
                "planned_validation_windows": self.planned_validation_windows,
                "minimum_finalized_for_comparison": self.minimum_finalized_for_comparison,
            },
        )


def validate_declaration(declaration: ExperimentDeclaration) -> None:
    """Reject any incomplete, unsafe, or non-offline experiment definition."""

    if not isinstance(declaration, ExperimentDeclaration):
        raise AnalysisInputError("validate_declaration requires an ExperimentDeclaration")
    for label, value in (
        ("name", declaration.name),
        ("symbol", declaration.symbol),
        ("timeframe", declaration.timeframe),
    ):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise AnalysisInputError(f"{label} must be a nonempty, trimmed string")
    if not declaration.dataset_ids or not all(
        isinstance(key, str) and key.strip() for key in declaration.dataset_ids
    ):
        raise AnalysisInputError("dataset_ids must be a nonempty tuple of offline dataset ids")
    if not isinstance(declaration.historical_start, datetime) or not isinstance(
        declaration.historical_end, datetime
    ):
        raise AnalysisInputError("historical range must be datetimes")
    if declaration.historical_end <= declaration.historical_start:
        raise AnalysisInputError("historical_end must be after historical_start")
    if not isinstance(declaration.robustness, RobustnessConfig):
        raise AnalysisInputError("robustness must be a RobustnessConfig (walk-forward settings)")
    if (
        type(declaration.planned_validation_windows) is not int
        or declaration.planned_validation_windows < 1
    ):
        raise AnalysisInputError("planned_validation_windows must be a positive integer")
    if (
        type(declaration.minimum_finalized_for_comparison) is not int
        or declaration.minimum_finalized_for_comparison < 1
    ):
        raise AnalysisInputError("minimum_finalized_for_comparison must be a positive integer")


def create_candidate_from_confirmed_hypothesis(
    *,
    baseline_config_hash: str,
    hypothesis: Hypothesis,
    confirmation: HypothesisConfirmation,
    delta: CandidateDelta,
    declaration: ExperimentDeclaration,
    rationale: str | None = None,
    expected_effect: str | None = None,
) -> CandidateExperimentDef:
    """Create the explicit candidate for a human-confirmed hypothesis.

    Requires a CONFIRMED confirmation for this hypothesis, a hypothesis
    baseline hash that matches the supplied baseline, a fully validated
    experiment declaration, and an allow-listed explicit delta. It never runs,
    tunes, or approves anything.
    """

    check_baseline_hash(baseline_config_hash)
    if not isinstance(hypothesis, Hypothesis):
        raise AnalysisInputError("create_candidate requires a Hypothesis")
    if not isinstance(confirmation, HypothesisConfirmation):
        raise AnalysisInputError("create_candidate requires a HypothesisConfirmation")
    if not isinstance(delta, CandidateDelta):
        raise AnalysisInputError("delta must be a validated CandidateDelta")
    if not isinstance(declaration, ExperimentDeclaration):
        raise AnalysisInputError("declaration must be an ExperimentDeclaration")
    if confirmation.hypothesis_id != hypothesis.hypothesis_id:
        raise AnalysisInputError("confirmation must reference this hypothesis")
    if confirmation.decision is not ConfirmationDecision.CONFIRMED:
        raise AnalysisInputError(
            "a candidate may be created only from an explicitly CONFIRMED hypothesis"
        )
    if hypothesis.baseline_hash != baseline_config_hash:
        raise AnalysisInputError(
            "hypothesis baseline_hash must match the supplied baseline configuration hash"
        )
    validate_declaration(declaration)
    use_rationale = rationale if rationale is not None else hypothesis.rationale
    use_effect = expected_effect if expected_effect is not None else hypothesis.expected_effect
    return build_candidate(
        baseline_hash=baseline_config_hash,
        deltas=(delta,),
        rationale=use_rationale,
        expected_effect=use_effect,
    )
