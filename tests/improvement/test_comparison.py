"""Phase 23B exact-Decimal comparison and sample gating."""

from __future__ import annotations

from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement import (
    CandidateEvidence,
    EngineProvenance,
    EvaluationStatus,
    FlatMetric,
    compare,
    human_comparison,
    machine_comparison,
)
from tests.improvement.helpers import DATASET_ID

BASELINE_HASH = "c" * 64
PROTOCOL = "protocol:ab" + "0" * 61


def _provenance(tag: str) -> EngineProvenance:
    return EngineProvenance(
        backtest_id="backtest:" + tag * 31,
        robustness_report_id="robustness-report:" + tag * 31,
        intelligence_report_id="intelligence-report:" + tag * 31,
        backtest_version="backtest-replay-v1",
        robustness_version="robustness-v1",
        intelligence_version="intelligence-v1",
    )


def _metric(
    dimension: str, group: str, metric: str, value: Decimal | None, sample: int
) -> FlatMetric:
    return FlatMetric(dimension, group, metric, value, sample)


def _overall_metrics(
    *, sample: int, win_rate: Decimal, finalized: Decimal, win_count: Decimal
) -> tuple[FlatMetric, ...]:
    return (
        _metric("overall", "all", "total_buy_signals", Decimal(sample), sample),
        _metric("overall", "all", "finalized_count", finalized, sample),
        _metric("overall", "all", "win_count", win_count, sample),
        _metric("overall", "all", "win_rate", win_rate, sample),
    )


def _evidence(subject: str, run: str, metrics: tuple[FlatMetric, ...]) -> CandidateEvidence:
    return CandidateEvidence(
        subject_id=subject,
        baseline_hash=BASELINE_HASH,
        run_id="evaluation:" + run,
        dataset_ids=(DATASET_ID,),
        protocol_identity=PROTOCOL,
        provenance=_provenance(run),
        metrics=metrics,
        limitations=("research-only",),
        status=EvaluationStatus.EVALUATED,
    )


def _pair(
    candidate_run: str = "c1", baseline_run: str = "b1"
) -> tuple[CandidateEvidence, CandidateEvidence]:
    baseline = _evidence(
        "baseline:" + baseline_run,
        baseline_run,
        _overall_metrics(
            sample=40, win_rate=Decimal("0.25"), finalized=Decimal(40), win_count=Decimal(10)
        ),
    )
    candidate = _evidence(
        "candidate:" + candidate_run,
        candidate_run,
        _overall_metrics(
            sample=40, win_rate=Decimal("0.5"), finalized=Decimal(40), win_count=Decimal(20)
        ),
    )
    return baseline, candidate


def test_exact_decimal_difference_is_candidate_minus_baseline() -> None:
    baseline, candidate = _pair()
    report = compare(baseline, candidate, minimum_finalized_for_comparison=30)
    by = {
        row.metric: row for row in report.rows if row.dimension == "overall" and row.group == "all"
    }
    assert by["win_rate"].difference == Decimal("0.25")
    assert by["win_rate"].candidate_value == Decimal("0.5")
    assert by["win_rate"].baseline_value == Decimal("0.25")
    assert by["finalized_count"].difference == Decimal(0)
    assert by["win_count"].difference == Decimal(10)
    assert by["win_rate"].conclusive is True


def test_values_are_exact_decimals_not_rounded() -> None:
    baseline = _evidence(
        "baseline:2",
        "b2",
        _overall_metrics(
            sample=40, win_rate=Decimal("0.33"), finalized=Decimal(40), win_count=Decimal(13)
        ),
    )
    candidate = _evidence(
        "candidate:2",
        "c2",
        _overall_metrics(
            sample=40, win_rate=Decimal("0.5"), finalized=Decimal(40), win_count=Decimal(20)
        ),
    )
    report = compare(baseline, candidate, minimum_finalized_for_comparison=30)
    row = report.rows_for("overall", "all", "win_rate")[0]
    assert isinstance(row.difference, Decimal)
    assert row.difference == Decimal("0.5") - Decimal("0.33")


def test_sample_gating_marks_below_minimum_inconclusive() -> None:
    baseline = _evidence(
        "baseline:3",
        "b3",
        _overall_metrics(
            sample=4, win_rate=Decimal("0.25"), finalized=Decimal(4), win_count=Decimal(1)
        ),
    )
    candidate = _evidence(
        "candidate:3",
        "c3",
        _overall_metrics(
            sample=8, win_rate=Decimal("0.5"), finalized=Decimal(8), win_count=Decimal(4)
        ),
    )
    report = compare(baseline, candidate, minimum_finalized_for_comparison=30)
    row = report.rows_for("overall", "all", "win_rate")[0]
    assert row.candidate_sample == 8
    assert row.baseline_sample == 4
    assert row.difference == Decimal("0.25")
    assert row.conclusive is False


def test_one_sided_structural_value_has_no_difference() -> None:
    baseline = _evidence("baseline:4", "b4", ())
    candidate = _evidence(
        "candidate:4", "c4", (_metric("regime", "trending", "win_rate", Decimal("0.6"), 40),)
    )
    report = compare(baseline, candidate, minimum_finalized_for_comparison=30)
    row = report.rows_for("regime", "trending", "win_rate")[0]
    assert row.candidate_value == Decimal("0.6")
    assert row.baseline_value is None
    assert row.difference is None
    assert row.conclusive is False


def test_compare_requires_same_baseline_hash() -> None:
    baseline = _evidence("baseline:5", "b5", ())
    mismatched = _evidence("candidate:5", "c5", ())
    candidate = CandidateEvidence(
        subject_id="candidate:5",
        baseline_hash="d" * 64,
        run_id="evaluation:c5",
        dataset_ids=(DATASET_ID,),
        protocol_identity=PROTOCOL,
        provenance=_provenance("c5"),
        status=EvaluationStatus.EVALUATED,
    )
    with pytest.raises(AnalysisInputError):
        compare(baseline, candidate, minimum_finalized_for_comparison=30)
    assert mismatched.subject_id == "candidate:5"


def test_compare_requires_same_protocol() -> None:
    baseline = _evidence("baseline:6", "b6", ())
    candidate = CandidateEvidence(
        subject_id="candidate:6",
        baseline_hash=BASELINE_HASH,
        run_id="evaluation:c6",
        dataset_ids=(DATASET_ID,),
        protocol_identity="protocol:xy" + "0" * 60,
        provenance=_provenance("c6"),
        status=EvaluationStatus.EVALUATED,
    )
    with pytest.raises(AnalysisInputError):
        compare(baseline, candidate, minimum_finalized_for_comparison=30)


def test_machine_and_human_comparison_are_deterministic() -> None:
    baseline, candidate = _pair(candidate_run="c7", baseline_run="b7")
    report = compare(baseline, candidate, minimum_finalized_for_comparison=30)
    machine = machine_comparison(report)
    assert machine_comparison(report) == machine
    assert machine["comparison_id"] == report.comparison_id
    assert machine["candidate_id"] == "candidate:c7"
    human = human_comparison(report)
    assert "candidate-versus-baseline" in human
    assert human_comparison(report) == human
    assert any("all.win_rate" in line and "0.25" in line for line in human.splitlines())
