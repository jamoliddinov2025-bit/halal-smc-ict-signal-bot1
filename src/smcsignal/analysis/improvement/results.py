"""Frozen Phase 23B evaluation and comparison result models.

These records describe what the existing Phase 20/21/22 engines already
reported for one explicitly declared candidate configuration (or the frozen
baseline) and how the candidate differs from the baseline. Every metric value
is copied verbatim from an engine report; Phase 23B computes no new metric.
Rates and averages are exact Decimal or ``None`` (never silently rounded), and
every difference is ``candidate - baseline`` with exact Decimal arithmetic.

Nothing in this module recommends, adopts, promotes, or approves a candidate.
Better historical results are evidence only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.evidence import check_baseline_hash, compose_id


class EvaluationStatus(StrEnum):
    """Outcome of one evaluation run of an explicitly declared configuration."""

    EVALUATED = "EVALUATED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class EngineProvenance:
    """Identities and versions of the upstream Phase 20/21/22 reports consumed."""

    backtest_id: str
    robustness_report_id: str
    intelligence_report_id: str
    backtest_version: str
    robustness_version: str
    intelligence_version: str

    def __post_init__(self) -> None:
        for name, value, prefix in (
            ("backtest_id", self.backtest_id, "backtest:"),
            ("robustness_report_id", self.robustness_report_id, "robustness-report:"),
            ("intelligence_report_id", self.intelligence_report_id, "intelligence-report:"),
        ):
            if not isinstance(value, str) or not value.startswith(prefix):
                raise AnalysisInputError(f"{name} must carry the {prefix} prefix")
        for name, value in (
            ("backtest_version", self.backtest_version),
            ("robustness_version", self.robustness_version),
            ("intelligence_version", self.intelligence_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise AnalysisInputError(f"{name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class FlatMetric:
    """One scalar metric value for one group, verbatim from an engine report.

    ``dimension`` is one of overall/setup/symbol/timeframe/month/regime/
    degradation/stability. ``sample_finalized`` is the finalized outcome count
    backing the value (0 where the concept does not apply, e.g. stability).
    ``value`` is an exact Decimal or ``None`` when the engine leaves the rate
    undefined (no finalized outcomes).
    """

    dimension: str
    group: str
    metric: str
    value: Decimal | None
    sample_finalized: int

    def __post_init__(self) -> None:
        for name, value in (
            ("dimension", self.dimension),
            ("group", self.group),
            ("metric", self.metric),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
        if type(self.sample_finalized) is not int or self.sample_finalized < 0:
            raise AnalysisInputError("sample_finalized must be a nonnegative integer")
        if self.value is not None and (
            not isinstance(self.value, Decimal) or not self.value.is_finite()
        ):
            raise AnalysisInputError("metric value must be a finite Decimal or None")


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Immutable evidence of evaluating one configuration through the engines.

    ``subject_id`` identifies what was run: the candidate's ``candidate_id``
    for a candidate, or ``baseline:<hash>`` for the frozen baseline.
    ``baseline_hash`` is the canonical SHA-256 of the frozen baseline
    configuration. All ids are deterministic content digests; none depend on
    wall-clock time, randomness, environment ordering, or display timestamps.
    """

    subject_id: str
    baseline_hash: str
    run_id: str
    dataset_ids: tuple[str, ...]
    protocol_identity: str
    provenance: EngineProvenance
    metrics: tuple[FlatMetric, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)
    status: EvaluationStatus = EvaluationStatus.EVALUATED

    def __post_init__(self) -> None:
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise AnalysisInputError("subject_id must be a nonempty string")
        check_baseline_hash(self.baseline_hash)
        if not isinstance(self.run_id, str) or not self.run_id.startswith("evaluation:"):
            raise AnalysisInputError("run_id must carry the evaluation: prefix")
        if not self.dataset_ids or not all(
            isinstance(k, str) and k.strip() for k in self.dataset_ids
        ):
            raise AnalysisInputError("dataset_ids must be a nonempty tuple of strings")
        if not isinstance(self.protocol_identity, str) or not self.protocol_identity.startswith(
            "protocol:"
        ):
            raise AnalysisInputError("protocol_identity must carry the protocol: prefix")
        if not isinstance(self.provenance, EngineProvenance):
            raise AnalysisInputError("provenance must be an EngineProvenance")
        if not all(isinstance(metric, FlatMetric) for metric in self.metrics):
            raise AnalysisInputError("metrics must be FlatMetric records")
        if not all(
            isinstance(limitation, str) and limitation.strip() for limitation in self.limitations
        ):
            raise AnalysisInputError("limitations must be nonempty strings")
        if not isinstance(self.status, EvaluationStatus):
            raise AnalysisInputError("status must be an EvaluationStatus")

    @property
    def overall_finalized(self) -> int:
        """Finalized sample of the overall evidence, or zero when absent."""
        for metric in self.metrics:
            if (
                metric.dimension == "overall"
                and metric.group == "all"
                and metric.metric == "finalized_count"
            ):
                value = metric.value
                if value is not None and value == value.to_integral_value():
                    return int(value)
        return 0


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """One ``candidate - baseline`` difference over one group/metric.

    ``difference`` is the exact Decimal difference when both sides define the
    value, otherwise ``None`` (a value exists on only one side). ``conclusive``
    is True only when both the candidate and baseline finalized samples reach
    the configured comparison minimum; inconclusive rows are never treated as
    evidence of improvement.
    """

    dimension: str
    group: str
    metric: str
    candidate_value: Decimal | None
    baseline_value: Decimal | None
    difference: Decimal | None
    candidate_sample: int
    baseline_sample: int
    conclusive: bool

    def __post_init__(self) -> None:
        for name, value in (
            ("dimension", self.dimension),
            ("group", self.group),
            ("metric", self.metric),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
        for label, raw in (
            ("candidate_value", self.candidate_value),
            ("baseline_value", self.baseline_value),
            ("difference", self.difference),
        ):
            if raw is not None and (not isinstance(raw, Decimal) or not raw.is_finite()):
                raise AnalysisInputError(f"{label} must be a finite Decimal or None")
        for label in (
            "candidate_sample",
            "baseline_sample",
        ):
            sample = getattr(self, label)
            if type(sample) is not int or sample < 0:
                raise AnalysisInputError(f"{label} must be a nonnegative integer")
        if type(self.conclusive) is not bool:
            raise AnalysisInputError("conclusive must be a boolean")


@dataclass(frozen=True, slots=True)
class CandidateComparisonReport:
    """One deterministic baseline-versus-candidate comparison.

    A better historical number is evidence only. This report never recommends,
    adopts, promotes, or approves; it never ranks or selects a candidate, and
    it carries no claim when the required finalized sample is not reached.
    """

    comparison_id: str
    baseline_hash: str
    candidate_id: str
    baseline_run_id: str
    candidate_run_id: str
    protocol_identity: str
    minimum_finalized_for_comparison: int
    rows: tuple[ComparisonRow, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        check_baseline_hash(self.baseline_hash)
        if not isinstance(self.comparison_id, str) or not self.comparison_id.startswith(
            "comparison:"
        ):
            raise AnalysisInputError("comparison_id must carry the comparison: prefix")
        if not isinstance(self.candidate_id, str) or not self.candidate_id.startswith("candidate:"):
            raise AnalysisInputError("candidate_id must carry the candidate: prefix")
        if not isinstance(self.protocol_identity, str) or not self.protocol_identity.startswith(
            "protocol:"
        ):
            raise AnalysisInputError("protocol_identity must carry the protocol: prefix")
        if (
            type(self.minimum_finalized_for_comparison) is not int
            or self.minimum_finalized_for_comparison < 1
        ):
            raise AnalysisInputError("minimum_finalized_for_comparison must be a positive integer")
        if not all(isinstance(row, ComparisonRow) for row in self.rows):
            raise AnalysisInputError("rows must be ComparisonRow records")
        if not all(
            isinstance(limitation, str) and limitation.strip() for limitation in self.limitations
        ):
            raise AnalysisInputError("limitations must be nonempty strings")

    def rows_for(self, dimension: str, group: str, metric: str) -> tuple[ComparisonRow, ...]:
        """Filter rows by exact dimension, group, and metric."""
        return tuple(
            row
            for row in self.rows
            if row.dimension == dimension and row.group == group and row.metric == metric
        )


def comparison_identity(
    *,
    baseline_run_id: str,
    candidate_run_id: str,
    protocol_identity: str,
) -> str:
    """Deterministic comparison identity from the two runs and the protocol."""
    return compose_id(
        "comparison",
        {
            "methodology": "improvement-eval-v1",
            "kind": "candidate-vs-baseline-comparison",
            "baseline_run_id": baseline_run_id,
            "candidate_run_id": candidate_run_id,
            "protocol_identity": protocol_identity,
        },
    )
