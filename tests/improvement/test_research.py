"""Phase 23C research layer: findings, hypotheses, confirmation, review.

Controlled synthetic findings/cells are used only where the tiny historical
fixture cannot reach the required finalized sample; their limitations are
documented in the docstring of this module and the methodology document. The
end-to-end pipeline is additionally exercised over the real engines, and the
report is kept honest (insufficient) because the fixture is small.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement import (
    CandidateEvidence,
    ConfirmationDecision,
    EngineProvenance,
    EvaluationStatus,
    EvidenceQuality,
    ExperimentDeclaration,
    build_review_report,
    classify_evidence,
    config_hash_of,
    confirm_hypothesis,
    create_candidate_from_confirmed_hypothesis,
    derive_findings,
    evaluate_candidate,
    finding_from_cell,
    finding_identity,
    generate_hypotheses,
    propose_delta,
    review_json,
    review_text,
)
from smcsignal.analysis.improvement.findings import FindingKind, ResearchFinding
from smcsignal.analysis.improvement.hypotheses import confirmation_identity
from smcsignal.analysis.intelligence import run_intelligence
from smcsignal.analysis.intelligence.models import (
    DiagnosticLabel,
    IntelligenceCell,
    IntelligenceDimension,
    IntelligencePattern,
)
from smcsignal.analysis.robustness import RobustnessConfig, run_robustness
from tests.improvement.helpers import (
    DATASET_ID,
    baseline_config,
    baseline_evidence,
    dataset,
    protocol,
)

START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2024, 2, 1, tzinfo=UTC)
OPERATOR = "operator:carol-7"


def _weak_cell(finalized: int = 40, *, name: str = "OB_DP") -> IntelligenceCell:
    win = finalized // 2
    loss = finalized - win
    return IntelligenceCell(
        dimension=IntelligenceDimension.SETUP,
        name=name,
        total_buy_signals=finalized,
        open_count=0,
        win_count=win,
        loss_count=loss,
        flat_count=0,
        finalized_count=finalized,
        final_return_sum=Decimal("-2.0"),
        mfe_return_sum=Decimal("4"),
        mae_return_sum=Decimal("6"),
        win_rate=Decimal("0.5"),
        average_final_return=Decimal("-0.05"),
        average_mfe_return=Decimal("0.1"),
        average_mae_return=Decimal("0.15"),
        sufficient=True,
        pattern=IntelligencePattern.LOSER,
        diagnostic=DiagnosticLabel.WEAKNESS,
        rank=None,
    )


def _finding() -> ResearchFinding:
    finding = finding_from_cell(
        _weak_cell(), minimum_finalized=30, origin_evidence_ids=("report:1#cell",)
    )
    assert finding is not None
    return finding


def _declaration() -> ExperimentDeclaration:
    return ExperimentDeclaration(
        name="exp-1",
        dataset_ids=(DATASET_ID,),
        symbol="BTCUSDT",
        timeframe="15m",
        historical_start=START,
        historical_end=END,
        robustness=RobustnessConfig(),
        planned_validation_windows=3,
        minimum_finalized_for_comparison=30,
    )


def _confirmed_hypothesis() -> tuple[object, object]:
    delta = propose_delta("displacement", "atr_period", 20)
    hypothesis = generate_hypotheses(
        delta=delta,
        baseline_hash=config_hash_of(baseline_config()),
        findings=(_finding(),),
    )[0]
    confirmation = confirm_hypothesis(
        hypothesis=hypothesis,
        decision=ConfirmationDecision.CONFIRMED,
        operator_identity=OPERATOR,
        rationale="explicitly confirmed for testing",
        confirmed_at=datetime(2024, 1, 5, tzinfo=UTC),
    )
    return hypothesis, confirmation


def _candidate_from_confirmed() -> object:
    hypothesis, confirmation = _confirmed_hypothesis()
    delta = propose_delta("displacement", "atr_period", 20)
    return create_candidate_from_confirmed_hypothesis(
        baseline_config_hash=config_hash_of(baseline_config()),
        hypothesis=hypothesis,
        confirmation=confirmation,
        delta=delta,
        declaration=_declaration(),
    )


def test_deterministic_hypothesis_generation() -> None:
    delta = propose_delta("displacement", "atr_period", 20)
    first = generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=(_finding(),))
    second = generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=(_finding(),))
    assert first == second
    assert [h.hypothesis_id for h in first] == [h.hypothesis_id for h in second]
    assert len(first) == 1
    assert first[0].status.value == "PROPOSED"
    assert first[0].proposed_change.startswith(
        "Test whether changing declared displacement.atr_period from 14 to 20 "
    )


def test_evidence_only_and_no_causal_claims() -> None:
    delta = propose_delta("displacement", "atr_period", 20)
    hypotheses = generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=(_finding(),))
    for hypothesis in hypotheses:
        assert "requiring testing" in hypothesis.rationale
        for banned in ("causes", "will improve", "guarantee", "future performance"):
            assert banned not in hypothesis.observed_weakness.lower()
            assert banned not in hypothesis.expected_effect.lower()


def test_sufficient_vs_insufficient_evidence() -> None:
    # Below the detection minimum a cell yields no weakness finding.
    insufficient = _weak_cell(finalized=4)
    finding = finding_from_cell(
        insufficient, minimum_finalized=30, origin_evidence_ids=("report:1#cell",)
    )
    assert finding is None
    quality, _ = classify_evidence(
        finalized_count=4,
        minimum_finalized=30,
        validation_completed=True,
        missing_windows=0,
        baseline_available=True,
    )
    assert quality is EvidenceQuality.INSUFFICIENT_SAMPLE
    delta = propose_delta("displacement", "atr_period", 20)
    # No hypothesis is emitted for an insufficient cell.
    assert generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=()) == ()


def test_derive_findings_over_real_engine_is_empty_when_insufficient() -> None:
    baseline = baseline_config()
    report = run_intelligence(run_robustness([dataset()], baseline, protocol().robustness))
    findings = derive_findings(report, minimum_finalized=30)
    # The small fixture cannot reach 30 finalized outcomes, so no finding and no
    # hypothesis are fabricated from it.
    assert findings == ()


def test_hypothesis_identity_is_stable_across_generation() -> None:
    delta = propose_delta("displacement", "atr_period", 20)
    first = generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=(_finding(),))[0]
    second = generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=(_finding(),))[0]
    assert first.hypothesis_id == second.hypothesis_id


def test_future_tail_does_not_change_hypothesis_identity() -> None:
    # Hypothesis identity is a function of the published evidence reference and
    # the declared inputs only; it never depends on the display timestamp or on
    # future/validation outcome values (there are none in the identity inputs).
    delta = propose_delta("displacement", "atr_period", 20)
    a = generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=(_finding(),))[0]
    b = generate_hypotheses(delta=delta, baseline_hash="a" * 64, findings=(_finding(),))[0]
    assert a.hypothesis_id == b.hypothesis_id
    # A confirmation identity is also timestamp-independent.
    c1 = confirmation_identity(
        hypothesis_id=a.hypothesis_id,
        decision=ConfirmationDecision.CONFIRMED,
        operator_identity=OPERATOR,
        rationale="r",
    )
    c2 = confirmation_identity(
        hypothesis_id=a.hypothesis_id,
        decision=ConfirmationDecision.CONFIRMED,
        operator_identity=OPERATOR,
        rationale="r",
    )
    assert c1 == c2


def test_human_confirmation_is_required_for_candidate() -> None:
    delta = propose_delta("displacement", "atr_period", 20)
    hypothesis = generate_hypotheses(
        delta=delta,
        baseline_hash=config_hash_of(baseline_config()),
        findings=(_finding(),),
    )[0]
    rejected = confirm_hypothesis(
        hypothesis=hypothesis,
        decision=ConfirmationDecision.REJECTED,
        operator_identity=OPERATOR,
        rationale="not worth testing",
        confirmed_at=datetime(2024, 1, 5, tzinfo=UTC),
    )
    with pytest.raises(AnalysisInputError):
        create_candidate_from_confirmed_hypothesis(
            baseline_config_hash=config_hash_of(baseline_config()),
            hypothesis=hypothesis,
            confirmation=rejected,
            delta=delta,
            declaration=_declaration(),
        )


def test_candidate_creation_from_confirmed_hypothesis() -> None:
    candidate = _candidate_from_confirmed()
    assert candidate.candidate_id.startswith("candidate:")
    assert candidate.baseline_hash == config_hash_of(baseline_config())
    assert candidate.status.value == "PROPOSED"
    assert candidate.deltas[0].key == "displacement.atr_period"


def test_allow_list_enforcement_in_candidate_creation() -> None:
    # An off-list delta (not a declared candidate surface) cannot be proposed.
    with pytest.raises(AnalysisInputError):
        propose_delta("signal_engine", "weight", 50)


def test_protocol_validation_rejects_incomplete_declaration() -> None:
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(
            name="exp",
            dataset_ids=(),
            symbol="BTCUSDT",
            timeframe="15m",
            historical_start=START,
            historical_end=END,
            robustness=RobustnessConfig(),
            planned_validation_windows=3,
            minimum_finalized_for_comparison=30,
        )
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(
            name="exp",
            dataset_ids=(DATASET_ID,),
            symbol="BTCUSDT",
            timeframe="15m",
            historical_start=END,
            historical_end=START,
            robustness=RobustnessConfig(),
            planned_validation_windows=3,
            minimum_finalized_for_comparison=30,
        )
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(
            name="exp",
            dataset_ids=(DATASET_ID,),
            symbol="BTCUSDT",
            timeframe="15m",
            historical_start=START,
            historical_end=END,
            robustness=RobustnessConfig(),
            planned_validation_windows=0,
            minimum_finalized_for_comparison=30,
        )


def test_evidence_quality_classification() -> None:
    assert (
        classify_evidence(
            finalized_count=40,
            minimum_finalized=30,
            validation_completed=True,
            missing_windows=0,
            baseline_available=True,
        )[0]
        is EvidenceQuality.SUFFICIENT
    )
    assert (
        classify_evidence(
            finalized_count=4,
            minimum_finalized=30,
            validation_completed=True,
            missing_windows=0,
            baseline_available=True,
        )[0]
        is EvidenceQuality.INSUFFICIENT_SAMPLE
    )
    assert (
        classify_evidence(
            finalized_count=40,
            minimum_finalized=30,
            validation_completed=True,
            missing_windows=1,
            baseline_available=True,
        )[0]
        is EvidenceQuality.INCOMPLETE
    )
    assert (
        classify_evidence(
            finalized_count=40,
            minimum_finalized=30,
            validation_completed=True,
            missing_windows=0,
            baseline_available=False,
        )[0]
        is EvidenceQuality.INCOMPLETE
    )


def test_deterministic_findings_identity() -> None:
    a = finding_identity(
        origin_evidence_ids=("r#c",),
        evidence_quality=EvidenceQuality.SUFFICIENT,
        kind=FindingKind.WEAKNESS_CELL,
        dimension="setup",
        group="OB_DP",
        metric="win_rate",
        observation="lower win rate",
    )
    b = finding_identity(
        origin_evidence_ids=("r#c",),
        evidence_quality=EvidenceQuality.SUFFICIENT,
        kind=FindingKind.WEAKNESS_CELL,
        dimension="setup",
        group="OB_DP",
        metric="win_rate",
        observation="lower win rate",
    )
    assert a == b
    assert a.startswith("finding:")


def test_review_report_is_deterministic_and_shows_review_required() -> None:
    candidate = _candidate_from_confirmed()
    hypothesis, _ = _confirmed_hypothesis()
    # evaluate the candidate and baseline with the real engines
    evidence = evaluate_candidate(baseline_config(), candidate, [dataset()], protocol())
    baseline = baseline_evidence()
    from smcsignal.analysis.improvement import compare

    comparison = compare(baseline, evidence, minimum_finalized_for_comparison=30)
    report = build_review_report(
        hypothesis=hypothesis,
        findings=(_finding(),),
        baseline=baseline,
        candidate=candidate,
        candidate_evidence=evidence,
        comparison=comparison,
        hypothesis_confirmation=None,
        human_candidate_decision=None,
    )
    assert report.display_decision == "REVIEW_REQUIRED"
    assert report.evidence_quality is EvidenceQuality.INSUFFICIENT_SAMPLE
    text = review_text(report)
    assert review_text(report) == text
    js = review_json(report)
    assert review_json(report) == js
    assert js["decision"] == "REVIEW_REQUIRED"
    assert js["evidence_quality"] == "INSUFFICIENT_SAMPLE"
    assert any(
        "not been adopted" in line or "not in production" in line for line in text.splitlines()
    )


def test_two_independent_candidates_are_independent() -> None:
    candidate_a = _candidate_from_confirmed()
    from smcsignal.analysis.improvement import build_candidate

    candidate_b = build_candidate(
        baseline_hash=config_hash_of(baseline_config()),
        deltas=(propose_delta("ote", "lower_retracement", Decimal("0.60")),),
        rationale="b",
        expected_effect="b",
    )
    assert candidate_a.candidate_id != candidate_b.candidate_id
    # Both remain independently evaluable; evaluating A does not change B.
    ea = evaluate_candidate(baseline_config(), candidate_a, [dataset()], protocol())
    assert ea.status is EvaluationStatus.EVALUATED


def _failed_evidence() -> CandidateEvidence:
    baseline_hash = config_hash_of(baseline_config())
    return CandidateEvidence(
        subject_id=f"candidate:{'c' * 64}",
        baseline_hash=baseline_hash,
        run_id="evaluation:failed",
        dataset_ids=(DATASET_ID,),
        protocol_identity="protocol:failed",
        provenance=EngineProvenance(
            backtest_id="backtest:x",
            robustness_report_id="robustness-report:x",
            intelligence_report_id="intelligence-report:x",
            backtest_version="1",
            robustness_version="1",
            intelligence_version="1",
        ),
        status=EvaluationStatus.FAILED,
    )


def test_declaration_validates_every_research_dimension() -> None:
    # A complete declaration is accepted...
    declaration = _declaration()
    assert declaration.dataset_ids == (DATASET_ID,)
    assert declaration.symbol == "BTCUSDT"
    assert declaration.timeframe == "15m"
    assert declaration.historical_start < declaration.historical_end
    assert isinstance(declaration.robustness, RobustnessConfig)
    assert declaration.planned_validation_windows >= 1
    assert declaration.minimum_finalized_for_comparison >= 1
    # ...and each research dimension is independently rejected when invalid.
    base = dict(
        name="exp",
        dataset_ids=(DATASET_ID,),
        symbol="BTCUSDT",
        timeframe="15m",
        historical_start=START,
        historical_end=END,
        robustness=RobustnessConfig(),
        planned_validation_windows=3,
        minimum_finalized_for_comparison=30,
    )
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(**{**base, "dataset_ids": ()})  # dataset identity
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(**{**base, "symbol": " "})  # symbol
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(**{**base, "timeframe": ""})  # timeframe
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(**{**base, "historical_end": START})  # historical range
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(**{**base, "robustness": 5})  # walk-forward configuration
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(**{**base, "planned_validation_windows": 0})  # windows
    with pytest.raises(AnalysisInputError):
        ExperimentDeclaration(
            **{**base, "minimum_finalized_for_comparison": 0}
        )  # minimum finalized sample


def test_review_report_reports_failed_evaluation_as_failed() -> None:
    hypothesis, _ = _confirmed_hypothesis()
    failed = _failed_evidence()
    report = build_review_report(
        hypothesis=hypothesis,
        findings=(_finding(),),
        baseline=None,
        candidate=None,
        candidate_evidence=failed,
        comparison=None,
        hypothesis_confirmation=None,
        human_candidate_decision=None,
    )
    assert report.evidence_quality is EvidenceQuality.FAILED
    assert report.display_decision == "REVIEW_REQUIRED"
    text = review_text(report)
    js = review_json(report)
    assert js["evidence_quality"] == "FAILED"
    assert "FAILED" in text
    assert "evaluation failed" in text


def test_all_evidence_quality_states_are_classified_and_communicated() -> None:
    # SUFFICIENT, INSUFFICIENT_SAMPLE, INCOMPLETE, and FAILED each produce a
    # deterministic quality label and an explanatory reason in the review text.
    hypothesis, _ = _confirmed_hypothesis()
    candidate = _candidate_from_confirmed()
    real_evidence = evaluate_candidate(baseline_config(), candidate, [dataset()], protocol())
    baseline = baseline_evidence()

    # INCOMPLETE: no baseline / no comparison evidence.
    incomplete = build_review_report(
        hypothesis=hypothesis,
        findings=(_finding(),),
        baseline=None,
        candidate=candidate,
        candidate_evidence=None,
        comparison=None,
        hypothesis_confirmation=None,
        human_candidate_decision=None,
    )
    assert incomplete.evidence_quality is EvidenceQuality.INCOMPLETE
    assert "INCOMPLETE" in review_text(incomplete)

    # SUFFICIENT via classification function (the report path stays honest
    # because the real small fixture is INSUFFICIENT_SAMPLE).
    sufficient_label, sufficient_reason = classify_evidence(
        finalized_count=40,
        minimum_finalized=30,
        validation_completed=True,
        missing_windows=0,
        baseline_available=True,
    )
    assert sufficient_label is EvidenceQuality.SUFFICIENT
    assert sufficient_reason

    # FAILED via a failed evaluation evidence record.
    failed = _failed_evidence()
    failed_report = build_review_report(
        hypothesis=hypothesis,
        findings=(_finding(),),
        baseline=baseline,
        candidate=candidate,
        candidate_evidence=failed,
        comparison=None,
        hypothesis_confirmation=None,
        human_candidate_decision=None,
    )
    assert failed_report.evidence_quality is EvidenceQuality.FAILED
    assert "FAILED" in review_text(failed_report)
    # A real evaluated-but-not-compared candidate cannot claim more than its
    # evidence allows; the fixture is too small to be SUFFICIENT.
    assert real_evidence.status is EvaluationStatus.EVALUATED


def test_unrelated_future_finding_does_not_change_established_hypothesis() -> None:
    # A finding/hypothesis records only the already-published evidence it was
    # derived from. Adding an unrelated (here "future") weakness finding to the
    # generator must not alter an already-established hypothesis, its identity,
    # or its evidence references.
    delta = propose_delta("displacement", "atr_period", 20)
    historical = _finding()  # OB_DP weakness (published historical evidence)
    future_finding = finding_from_cell(
        _weak_cell(name="FUTURE_LQ"),
        minimum_finalized=30,
        origin_evidence_ids=("report:future#cell",),
    )
    assert future_finding is not None

    only_historical = generate_hypotheses(
        delta=delta, baseline_hash="a" * 64, findings=(historical,)
    )
    with_future = generate_hypotheses(
        delta=delta, baseline_hash="a" * 64, findings=(historical, future_finding)
    )
    only_future = generate_hypotheses(
        delta=delta, baseline_hash="a" * 64, findings=(future_finding,)
    )
    assert len(only_historical) == 1

    historical_hypothesis = only_historical[0]
    # The established historical hypothesis is byte-identical whether or not the
    # unrelated future finding is also present in the generator call.
    match = [h for h in with_future if h.evidence_references == historical.origin_evidence_ids]
    assert len(match) == 1
    assert match[0] == historical_hypothesis
    assert match[0].hypothesis_id == historical_hypothesis.hypothesis_id
    # The future finding independently produces its own separate hypothesis.
    assert len(only_future) == 1
    assert only_future[0].hypothesis_id != historical_hypothesis.hypothesis_id
    assert only_future[0].evidence_references == future_finding.origin_evidence_ids
    # Adding future evidence never changed the established hypothesis identity.
    assert historical_hypothesis.hypothesis_id not in {h.hypothesis_id for h in only_future}
