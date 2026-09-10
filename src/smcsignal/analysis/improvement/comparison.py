"""Deterministic baseline-versus-candidate comparison with exact Decimal deltas."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.results import (
    CandidateComparisonReport,
    CandidateEvidence,
    ComparisonRow,
    comparison_identity,
)

# Dimensions that describe robustness/degradation stability rather than a
# finalized outcome sample; they are never treated as conclusive improvement
# evidence because they carry no finalized outcome count.
_DESCRIPTIVE_DIMENSIONS = frozenset({"degradation", "stability"})


def _index(evidence: CandidateEvidence) -> dict[tuple[str, str, str], Decimal | None]:
    return {
        (metric.dimension, metric.group, metric.metric): metric.value for metric in evidence.metrics
    }


def _sample_index(evidence: CandidateEvidence) -> dict[tuple[str, str, str], int]:
    return {
        (metric.dimension, metric.group, metric.metric): metric.sample_finalized
        for metric in evidence.metrics
    }


def compare(
    baseline: CandidateEvidence,
    candidate: CandidateEvidence,
    *,
    minimum_finalized_for_comparison: int,
) -> CandidateComparisonReport:
    """Compare one baseline evidence against one candidate evidence.

    Every difference is ``candidate - baseline`` with exact Decimal arithmetic
    (never rounded). A row is ``conclusive`` only when both the candidate and
    the baseline finalized sample reach ``minimum_finalized_for_comparison``
    and the dimension carries a finalized outcome sample. Below that the raw
    counts are preserved and no row may be read as evidence of improvement.
    """

    if not isinstance(baseline, CandidateEvidence):
        raise AnalysisInputError("baseline must be CandidateEvidence")
    if not isinstance(candidate, CandidateEvidence):
        raise AnalysisInputError("candidate must be CandidateEvidence")
    if baseline.baseline_hash != candidate.baseline_hash:
        raise AnalysisInputError("baseline and candidate must share the same baseline hash")
    if baseline.protocol_identity != candidate.protocol_identity:
        raise AnalysisInputError("baseline and candidate must share the same protocol")
    if baseline.dataset_ids != candidate.dataset_ids:
        raise AnalysisInputError("baseline and candidate must cover the same datasets")
    if type(minimum_finalized_for_comparison) is not int or minimum_finalized_for_comparison < 1:
        raise AnalysisInputError("minimum_finalized_for_comparison must be a positive integer")

    candidate_values = _index(candidate)
    baseline_values = _index(baseline)
    candidate_samples = _sample_index(candidate)
    baseline_samples = _sample_index(baseline)

    keys = set(candidate_values) | set(baseline_values)
    rows: list[ComparisonRow] = []
    for dimension, group, metric in sorted(keys, key=lambda key: (key[0], key[1], key[2])):
        candidate_value = candidate_values.get((dimension, group, metric))
        baseline_value = baseline_values.get((dimension, group, metric))
        difference: Decimal | None = None
        if candidate_value is not None and baseline_value is not None:
            difference = candidate_value - baseline_value
        candidate_sample = candidate_samples.get((dimension, group, metric), 0)
        baseline_sample = baseline_samples.get((dimension, group, metric), 0)
        conclusive = (
            dimension not in _DESCRIPTIVE_DIMENSIONS
            and candidate_sample >= minimum_finalized_for_comparison
            and baseline_sample >= minimum_finalized_for_comparison
        )
        rows.append(
            ComparisonRow(
                dimension=dimension,
                group=group,
                metric=metric,
                candidate_value=candidate_value,
                baseline_value=baseline_value,
                difference=difference,
                candidate_sample=candidate_sample,
                baseline_sample=baseline_sample,
                conclusive=conclusive,
            )
        )

    return CandidateComparisonReport(
        comparison_id=comparison_identity(
            baseline_run_id=baseline.run_id,
            candidate_run_id=candidate.run_id,
            protocol_identity=candidate.protocol_identity,
        ),
        baseline_hash=candidate.baseline_hash,
        candidate_id=candidate.subject_id,
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        protocol_identity=candidate.protocol_identity,
        minimum_finalized_for_comparison=minimum_finalized_for_comparison,
        rows=tuple(rows),
        limitations=candidate.limitations,
    )
