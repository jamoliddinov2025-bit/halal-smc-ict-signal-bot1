"""Phase 23A approval state machine, human-only approval, FAILED-is-terminal."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement import (
    CandidateExperimentDef,
    CandidateStatus,
    apply_human_decision,
    build_candidate,
    build_decision,
    propose_delta,
    transition,
)
from smcsignal.analysis.improvement.models import HumanDecisionRecord

BH = "e" * 64
OPERATOR = "operator:alice-12bq3"


def _proposed() -> CandidateExperimentDef:
    return build_candidate(
        baseline_hash=BH,
        deltas=(propose_delta("displacement", "atr_period", 20),),
        rationale="widen lookback",
        expected_effect="cleaner",
    )


def _walk_to_review() -> CandidateExperimentDef:
    candidate = _proposed()
    return transition(
        transition(
            transition(candidate, CandidateStatus.EXPERIMENTAL),
            CandidateStatus.EVALUATED,
        ),
        CandidateStatus.REVIEW_REQUIRED,
    )


def test_automatic_path_reaches_review_required() -> None:
    candidate = _walk_to_review()
    assert candidate.status is CandidateStatus.REVIEW_REQUIRED


def test_proposed_cannot_skip_ahead() -> None:
    candidate = _proposed()
    for target in (CandidateStatus.REVIEW_REQUIRED, CandidateStatus.EVALUATED):
        with pytest.raises(AnalysisInputError):
            transition(candidate, target)


def test_automatic_transition_cannot_approve_or_reject() -> None:
    candidate = _walk_to_review()
    for target in (CandidateStatus.APPROVED, CandidateStatus.REJECTED):
        with pytest.raises(AnalysisInputError):
            transition(candidate, target)


def test_only_human_decision_may_approve() -> None:
    review = _walk_to_review()
    decision = build_decision(
        candidate=review,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="looks sound against evidence",
        evidence_id="comparison:" + "0" * 64,
    )
    result = apply_human_decision(review, decision)
    assert result.status is CandidateStatus.APPROVED
    # original remains untouched (immutability)
    assert review.status is CandidateStatus.REVIEW_REQUIRED


def test_only_human_decision_may_reject() -> None:
    review = _walk_to_review()
    decision = build_decision(
        candidate=review,
        decision=CandidateStatus.REJECTED,
        operator_identity=OPERATOR,
        rationale="not an improvement",
    )
    assert apply_human_decision(review, decision).status is CandidateStatus.REJECTED


def test_human_decision_requires_review_required_state() -> None:
    experimental = transition(_proposed(), CandidateStatus.EXPERIMENTAL)
    decision = build_decision(
        candidate=experimental,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="early",
    )
    with pytest.raises(AnalysisInputError):
        apply_human_decision(experimental, decision)


def test_failed_is_terminal_and_never_approvable() -> None:
    experimental = transition(_proposed(), CandidateStatus.EXPERIMENTAL)
    failed = transition(experimental, CandidateStatus.FAILED)
    assert failed.status is CandidateStatus.FAILED
    for target in (CandidateStatus.EVALUATED, CandidateStatus.REVIEW_REQUIRED):
        with pytest.raises(AnalysisInputError):
            transition(failed, target)
    decision = build_decision(
        candidate=failed,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="retro",
    )
    with pytest.raises(AnalysisInputError):
        apply_human_decision(failed, decision)


def _approve(candidate: CandidateExperimentDef) -> CandidateExperimentDef:
    decision = build_decision(
        candidate=candidate,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="ok",
    )
    return apply_human_decision(candidate, decision)


def _reject(candidate: CandidateExperimentDef) -> CandidateExperimentDef:
    decision = build_decision(
        candidate=candidate,
        decision=CandidateStatus.REJECTED,
        operator_identity=OPERATOR,
        rationale="no",
    )
    return apply_human_decision(candidate, decision)


def test_approved_and_rejected_are_terminal() -> None:
    for terminal in (_approve(_walk_to_review()), _reject(_walk_to_review())):
        for target in (
            CandidateStatus.EXPERIMENTAL,
            CandidateStatus.EVALUATED,
            CandidateStatus.REVIEW_REQUIRED,
        ):
            with pytest.raises(AnalysisInputError):
                transition(terminal, target)


def test_human_decision_must_match_candidate() -> None:
    review = _walk_to_review()
    other = build_candidate(
        baseline_hash="d" * 64,
        deltas=(propose_delta("ote", "lower_retracement", Decimal("0.60")),),
        rationale="other",
        expected_effect="other",
    )
    foreign = build_decision(
        candidate=other,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="x",
    )
    with pytest.raises(AnalysisInputError):
        apply_human_decision(review, foreign)


def test_human_decision_record_carries_provenance() -> None:
    review = _walk_to_review()
    decision = build_decision(
        candidate=review,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="explicit sign-off",
        evidence_id="comparison:" + "a" * 64,
    )
    assert isinstance(decision, HumanDecisionRecord)
    assert decision.candidate_id == review.candidate_id
    assert decision.operator_identity == OPERATOR
    assert decision.rationale == "explicit sign-off"
    assert decision.decision_id.startswith("decision:")
    assert decision.evidence_id is not None


def test_operator_identity_is_never_inferred_or_empty() -> None:
    review = _walk_to_review()
    with pytest.raises(AnalysisInputError):
        build_decision(
            candidate=review,
            decision=CandidateStatus.APPROVED,
            operator_identity="   ",
            rationale="r",
        )


def test_decision_must_be_approved_or_rejected() -> None:
    review = _walk_to_review()
    for status in (CandidateStatus.REVIEW_REQUIRED, CandidateStatus.FAILED):
        with pytest.raises(AnalysisInputError):
            build_decision(
                candidate=review,
                decision=status,
                operator_identity=OPERATOR,
                rationale="x",
            )


def test_human_decision_identity_is_deterministic() -> None:
    review = _walk_to_review()
    first = build_decision(
        candidate=review,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="sign-off",
    )
    second = build_decision(
        candidate=review,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="sign-off",
    )
    assert first.decision_id == second.decision_id


def test_no_automatic_promotion_mechanism_exists() -> None:
    from smcsignal.analysis import improvement

    exposed = {name for name in dir(improvement)}
    for banned in ("promote", "activate", "release", "deploy", "apply_to_strategy"):
        assert not any(banned in name.lower() for name in exposed)


def test_decision_record_is_immutable() -> None:
    review = _walk_to_review()
    decision = build_decision(
        candidate=review,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="sign-off",
    )
    with pytest.raises(FrozenInstanceError):
        decision.rationale = "changed"  # type: ignore[misc]
