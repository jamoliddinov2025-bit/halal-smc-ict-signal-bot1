"""Phase 23B candidate evaluation: isolation, determinism, status handling."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from smcsignal.analysis.backtest import run_backtest
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement import (
    EvaluationStatus,
    apply_candidate_deltas,
    build_candidate,
    config_hash_of,
    evaluate_candidate,
    finish_experiment,
    propose_delta,
    start_experiment,
)
from smcsignal.analysis.improvement.models import (
    CandidateExperimentDef,
    CandidateStatus,
    build_decision,
)
from smcsignal.analysis.improvement.results import (
    CandidateEvidence,
    EngineProvenance,
)
from smcsignal.analysis.improvement.state import apply_human_decision, transition
from smcsignal.analysis.intelligence import run_intelligence
from smcsignal.analysis.robustness import run_robustness
from tests.improvement.helpers import baseline_config, baseline_evidence, dataset, protocol

OPERATOR = "operator:bot-1"


def _candidate() -> CandidateExperimentDef:
    return build_candidate(
        baseline_hash=config_hash_of(baseline_config()),
        deltas=(propose_delta("displacement", "atr_period", 20),),
        rationale="widen lookback",
        expected_effect="cleaner",
    )


def _failed_evidence(candidate: CandidateExperimentDef) -> CandidateEvidence:
    return CandidateEvidence(
        subject_id=candidate.candidate_id,
        baseline_hash=config_hash_of(baseline_config()),
        run_id="evaluation:" + "f" * 62,
        dataset_ids=(dataset().dataset_id,),
        protocol_identity=protocol().identity(),
        provenance=_provenance(),
        status=EvaluationStatus.FAILED,
    )


def _provenance() -> EngineProvenance:
    return EngineProvenance(
        backtest_id="backtest:" + "b" * 62,
        robustness_report_id="robustness-report:" + "r" * 62,
        intelligence_report_id="intelligence-report:" + "i" * 62,
        backtest_version="backtest-replay-v1",
        robustness_version="robustness-v1",
        intelligence_version="intelligence-v1",
    )


def test_candidate_evaluation_runs_engines_and_yields_evidence() -> None:
    candidate = _candidate()
    evidence = evaluate_candidate(baseline_config(), candidate, [dataset()], protocol())
    assert evidence.status is EvaluationStatus.EVALUATED
    assert evidence.subject_id == candidate.candidate_id
    assert evidence.baseline_hash == config_hash_of(baseline_config())
    assert evidence.run_id.startswith("evaluation:")
    assert len(evidence.dataset_ids) == 1
    assert evidence.metrics


def test_evaluating_candidate_does_not_mutate_baseline() -> None:
    baseline = baseline_config()
    before = config_hash_of(baseline)
    candidate = _candidate()
    candidate_config = apply_candidate_deltas(baseline, candidate.deltas)
    assert candidate_config is not baseline
    assert candidate_config.displacement.atr_period == 20
    assert baseline.displacement.atr_period == 14
    evaluate_candidate(baseline, candidate, [dataset()], protocol())
    assert config_hash_of(baseline) == before
    assert baseline.displacement.atr_period == 14


def test_baseline_evaluation_is_deterministic_and_preserved() -> None:
    first = baseline_evidence()
    second = baseline_evidence()
    assert first == second
    assert first.run_id == second.run_id
    assert first.provenance.intelligence_report_id == second.provenance.intelligence_report_id


def test_baseline_evaluation_is_byte_identical_to_direct_engines() -> None:
    baseline = baseline_config()
    evidence = baseline_evidence()
    direct = run_intelligence(run_robustness([dataset()], baseline, protocol().robustness))
    assert evidence.provenance.intelligence_report_id == direct.report_id
    backtest = run_backtest([dataset()], baseline)
    assert evidence.provenance.backtest_id == backtest.backtest_id


def test_repeated_candidate_evaluation_is_deterministic() -> None:
    candidate = _candidate()
    first = evaluate_candidate(baseline_config(), candidate, [dataset()], protocol())
    second = evaluate_candidate(baseline_config(), candidate, [dataset()], protocol())
    assert first == second
    assert first.run_id == second.run_id


def test_candidate_identity_stable_and_anchored() -> None:
    a = _candidate()
    b = build_candidate(
        baseline_hash=config_hash_of(baseline_config()),
        deltas=(propose_delta("displacement", "atr_period", 20),),
        rationale="different prose does not change the identity",
        expected_effect="also ignored",
    )
    assert a.candidate_id == b.candidate_id
    assert a.candidate_id.startswith("candidate:")


def test_changing_a_delta_creates_a_new_candidate_and_run() -> None:
    a = _candidate()
    b = build_candidate(
        baseline_hash=config_hash_of(baseline_config()),
        deltas=(propose_delta("ote", "lower_retracement", Decimal("0.60")),),
        rationale="r",
        expected_effect="e",
    )
    assert a.candidate_id != b.candidate_id
    ea = evaluate_candidate(baseline_config(), a, [dataset()], protocol())
    eb = evaluate_candidate(baseline_config(), b, [dataset()], protocol())
    assert ea.run_id != eb.run_id


def test_candidate_a_and_b_are_isolated_from_each_other() -> None:
    a = build_candidate(
        baseline_hash=config_hash_of(baseline_config()),
        deltas=(propose_delta("displacement", "atr_period", 20),),
        rationale="a",
        expected_effect="a",
    )
    b = build_candidate(
        baseline_hash=config_hash_of(baseline_config()),
        deltas=(propose_delta("ote", "lower_retracement", Decimal("0.60")),),
        rationale="b",
        expected_effect="b",
    )
    baseline = baseline_config()
    evaluate_candidate(baseline, a, [dataset()], protocol())
    fresh_b = evaluate_candidate(baseline, b, [dataset()], protocol())
    after_b = evaluate_candidate(baseline, b, [dataset()], protocol())
    assert fresh_b == after_b
    assert config_hash_of(baseline) == config_hash_of(baseline_config())


def test_evaluation_failure_moves_experiment_to_failed() -> None:
    candidate = _candidate()
    experimental = start_experiment(candidate)
    assert experimental.status is CandidateStatus.EXPERIMENTAL
    # A real evaluation mismatch (protocol vs dataset identity) is a failure.
    wrong_protocol = protocol()
    wrong = type(wrong_protocol)(
        name="other",
        dataset_ids=("other-dataset",),
        robustness=wrong_protocol.robustness,
        intelligence=wrong_protocol.intelligence,
        minimum_finalized_for_comparison=30,
    )
    with pytest.raises(AnalysisInputError):
        evaluate_candidate(baseline_config(), candidate, [dataset()], wrong)
    failed = finish_experiment(experimental, _failed_evidence(candidate))
    assert failed.status is CandidateStatus.FAILED


def test_failed_can_never_become_approved() -> None:
    candidate = _candidate()
    experimental = start_experiment(candidate)
    failed = finish_experiment(experimental, _failed_evidence(candidate))
    assert failed.status is CandidateStatus.FAILED
    with pytest.raises(AnalysisInputError):
        transition(failed, CandidateStatus.EVALUATED)
    decision = build_decision(
        candidate=failed,
        decision=CandidateStatus.APPROVED,
        operator_identity=OPERATOR,
        rationale="x",
    )
    with pytest.raises(AnalysisInputError):
        apply_human_decision(failed, decision)


def test_finish_experiment_evaluated_is_not_approval() -> None:
    candidate = _candidate()
    experimental = start_experiment(candidate)
    evidence = evaluate_candidate(baseline_config(), candidate, [dataset()], protocol())
    evaluated = finish_experiment(experimental, evidence)
    assert evaluated.status is CandidateStatus.EVALUATED
    assert evaluated.status is not CandidateStatus.APPROVED


def test_evidence_and_baseline_reference_are_frozen() -> None:
    evidence = baseline_evidence()
    with pytest.raises(FrozenInstanceError):
        evidence.run_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        evidence.metrics = ()  # type: ignore[misc]
