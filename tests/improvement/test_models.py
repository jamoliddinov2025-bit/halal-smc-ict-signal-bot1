"""Phase 23A immutable models: identities, baseline isolation, hypotheses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement import (
    CandidateDelta,
    CandidateExperimentDef,
    CandidateStatus,
    HypothesisStatus,
    build_candidate,
    build_hypothesis,
    propose_delta,
)
from smcsignal.analysis.improvement.evidence import canonical_bytes, compose_id

BH = "f" * 64  # canonical 64-char hex baseline hash


def _delta(setting: str = "atr_period", new_value: int = 20) -> CandidateDelta:
    return propose_delta("displacement", setting, new_value)


def _candidate(
    rationale: str = "r", expected_effect: str = "e", baseline_hash: str = BH
) -> CandidateExperimentDef:
    return build_candidate(
        baseline_hash=baseline_hash,
        deltas=(_delta(),),
        rationale=rationale,
        expected_effect=expected_effect,
    )


def test_candidate_is_created_proposed() -> None:
    candidate = _candidate(rationale="widen lookback", expected_effect="fewer false")
    assert candidate.status is CandidateStatus.PROPOSED
    assert candidate.candidate_id.startswith("candidate:")


def test_candidate_is_immutable() -> None:
    candidate = _candidate()
    with pytest.raises(FrozenInstanceError):
        candidate.status = CandidateStatus.APPROVED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.deltas = ()  # type: ignore[misc]


def test_deltas_are_immutable_and_sorted() -> None:
    candidate = build_candidate(
        baseline_hash=BH,
        deltas=(propose_delta("ote", "upper_retracement", Decimal("0.85")), _delta()),
        rationale="r",
        expected_effect="e",
    )
    with pytest.raises(FrozenInstanceError):
        candidate.deltas[0].new_value = 99  # type: ignore[misc]
    keys = [delta.key for delta in candidate.deltas]
    assert keys == sorted(keys)


def test_candidate_identity_is_order_independent() -> None:
    a = build_candidate(
        baseline_hash=BH,
        deltas=(_delta(), propose_delta("ote", "upper_retracement", Decimal("0.85"))),
        rationale="first ordering",
        expected_effect="any prose",
    )
    b = build_candidate(
        baseline_hash=BH,
        deltas=(propose_delta("ote", "upper_retracement", Decimal("0.85")), _delta()),
        rationale="different prose only",
        expected_effect="still same identity",
    )
    assert a.candidate_id == b.candidate_id


def test_candidate_identity_changes_with_delta_or_baseline() -> None:
    base = _candidate()
    changed_value = build_candidate(
        baseline_hash=BH,
        deltas=(_delta(new_value=22),),
        rationale="r",
        expected_effect="e",
    )
    changed_baseline = _candidate(baseline_hash="a" * 64)
    assert base.candidate_id != changed_value.candidate_id
    assert base.candidate_id != changed_baseline.candidate_id


def test_prose_does_not_alter_candidate_identity() -> None:
    assert (
        _candidate(rationale="anything").candidate_id
        == _candidate(rationale="completely different prose").candidate_id
    )


def test_canonical_serialization_underpins_identity() -> None:
    delta = _delta()
    payload = {
        "methodology": "improvement-v1",
        "kind": "candidate-experiment",
        "baseline_hash": BH,
        "deltas": [
            {
                "component": delta.component,
                "setting": delta.setting,
                "old_value": delta.old_value,
                "new_value": delta.new_value,
            }
        ],
    }
    # The candidate identity is exactly the canonical digest of that payload.
    assert compose_id("candidate", payload) == _candidate().candidate_id
    # canonical_bytes is deterministic across structurally equal inputs
    assert canonical_bytes(payload) == canonical_bytes(
        {
            "methodology": "improvement-v1",
            "kind": "candidate-experiment",
            "baseline_hash": BH,
            "deltas": [
                {
                    "component": "displacement",
                    "setting": "atr_period",
                    "old_value": 14,
                    "new_value": 20,
                }
            ],
        }
    )


def test_candidate_never_mutates_a_baseline_config() -> None:
    # A candidate stores a baseline_hash reference and declared deltas only;
    # it holds no configuration object and carries no apply/to_config method.
    candidate = _candidate()
    assert candidate.baseline_hash == BH
    assert not hasattr(candidate, "apply")
    assert not hasattr(candidate, "to_config")
    assert candidate.deltas[0].old_value == 14  # anchored to real baseline value


def test_delta_old_value_must_equal_frozen_baseline() -> None:
    with pytest.raises(AnalysisInputError):
        CandidateDelta("displacement", "atr_period", old_value=999, new_value=20)
    with pytest.raises(AnalysisInputError):
        CandidateDelta(
            "ote",
            "lower_retracement",
            old_value=Decimal("0.5"),
            new_value=Decimal("0.6"),
        )


def test_delta_new_value_must_change_and_be_valid() -> None:
    with pytest.raises(AnalysisInputError):
        CandidateDelta("displacement", "atr_period", old_value=14, new_value=14)  # no change
    with pytest.raises(AnalysisInputError):
        CandidateDelta("displacement", "atr_period", old_value=14, new_value=0)  # invalid


def test_conflicting_or_duplicate_deltas_rejected() -> None:
    with pytest.raises(AnalysisInputError):
        build_candidate(
            baseline_hash=BH,
            deltas=(
                propose_delta("ote", "upper_retracement", Decimal("0.85")),
                propose_delta("ote", "upper_retracement", Decimal("0.90")),
            ),
            rationale="r",
            expected_effect="e",
        )


def test_candidate_requires_at_least_one_delta() -> None:
    with pytest.raises(AnalysisInputError):
        build_candidate(baseline_hash=BH, deltas=(), rationale="r", expected_effect="e")


def test_delta_outside_allow_list_is_rejected() -> None:
    with pytest.raises(AnalysisInputError):
        propose_delta("signal_engine", "weight", 50)
    with pytest.raises(AnalysisInputError):
        propose_delta("setup_quality", "enabled", True)


def test_invalid_baseline_hash_rejected() -> None:
    with pytest.raises(AnalysisInputError):
        _candidate(baseline_hash="not-a-hash")


def test_hypothesis_is_proposal_only_and_proposed() -> None:
    hypothesis = build_hypothesis(
        title="Lengthen displacement lookback",
        affected_component="displacement",
        observed_weakness="false confirmation noise",
        proposed_change="raise atr_period",
        rationale="literature review",
        expected_effect="cleaner confirmations",
        scope="offline-research",
        baseline_hash=BH,
    )
    assert hypothesis.status is HypothesisStatus.PROPOSED
    assert hypothesis.hypothesis_id.startswith("hypothesis:")
    # hypotheses change nothing and run nothing
    assert not hasattr(hypothesis, "apply")
    assert not hasattr(hypothesis, "run")


def test_hypothesis_identity_is_deterministic() -> None:
    kwargs = dict(
        title="T",
        affected_component="displacement",
        observed_weakness="w",
        proposed_change="pc",
        rationale="r",
        expected_effect="e",
        scope="s",
        baseline_hash=BH,
    )
    a = build_hypothesis(**kwargs)
    b = build_hypothesis(**dict(kwargs, rationale="different rationale"))
    assert a.hypothesis_id == b.hypothesis_id  # prose excluded from identity


def test_hypothesis_rejects_empty_prose() -> None:
    with pytest.raises(AnalysisInputError):
        build_hypothesis(
            title="",
            affected_component="d",
            observed_weakness="w",
            proposed_change="pc",
            rationale="r",
            expected_effect="e",
            scope="s",
            baseline_hash=BH,
        )
