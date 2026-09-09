"""Candidate approval state machine and explicit human decision application.

Automatic transitions never produce APPROVED or REJECTED. Only
``apply_human_decision`` — the explicit human decision operation carrying a
``HumanDecisionRecord`` — may move a candidate into APPROVED or REJECTED. A
FAILED experiment is terminal and can never become approvable. ``APPROVED``
means approved for future consideration only; there is no promotion mechanism.
"""

from __future__ import annotations

from dataclasses import replace

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.models import (
    CandidateExperimentDef,
    CandidateStatus,
    HumanDecisionRecord,
)

# Automatic (non-human) transitions only. APPROVED and REJECTED are reachable
# exclusively through apply_human_decision.
AUTO_TRANSITIONS: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    CandidateStatus.PROPOSED: frozenset({CandidateStatus.EXPERIMENTAL}),
    CandidateStatus.EXPERIMENTAL: frozenset({CandidateStatus.EVALUATED, CandidateStatus.FAILED}),
    CandidateStatus.EVALUATED: frozenset({CandidateStatus.REVIEW_REQUIRED}),
    CandidateStatus.REVIEW_REQUIRED: frozenset(),
    CandidateStatus.APPROVED: frozenset(),
    CandidateStatus.REJECTED: frozenset(),
    CandidateStatus.FAILED: frozenset(),
}

HUMAN_ONLY = frozenset({CandidateStatus.APPROVED, CandidateStatus.REJECTED})


def can_transition(current: CandidateStatus, target: CandidateStatus) -> bool:
    """True when an automatic (non-human) transition is permitted."""
    return target in AUTO_TRANSITIONS[current]


def transition(
    candidate: CandidateExperimentDef, target: CandidateStatus
) -> CandidateExperimentDef:
    """Advance a candidate through a permitted automatic transition.

    Raises for any transition to APPROVED or REJECTED (human-only), for an
    unknown target, or for a move that leaves a terminal state.
    """
    if not isinstance(target, CandidateStatus):
        raise AnalysisInputError("transition target must be a CandidateStatus")
    if target in HUMAN_ONLY:
        raise AnalysisInputError(
            "APPROVED and REJECTED require an explicit human decision; "
            "no automatic transition may set them"
        )
    if not can_transition(candidate.status, target):
        raise AnalysisInputError(
            f"cannot transition candidate from {candidate.status.value} to {target.value}"
        )
    return replace(candidate, status=target)


def apply_human_decision(
    candidate: CandidateExperimentDef, decision: HumanDecisionRecord
) -> CandidateExperimentDef:
    """Apply an explicit human APPROVED/REJECTED decision to a candidate.

    The candidate must be REVIEW_REQUIRED and the decision must reference this
    candidate. Returns a new immutable candidate; the original is unchanged.
    """
    if not isinstance(decision, HumanDecisionRecord):
        raise AnalysisInputError("a human decision requires a HumanDecisionRecord")
    if decision.candidate_id != candidate.candidate_id:
        raise AnalysisInputError("decision candidate_id must match the candidate")
    if candidate.status is not CandidateStatus.REVIEW_REQUIRED:
        raise AnalysisInputError("a candidate may be decided only after it reaches REVIEW_REQUIRED")
    return replace(candidate, status=decision.decision)
