"""Deterministic research-review reports for a hypothesis and candidate.

A review report ties together a hypothesis, the descriptive findings behind it,
the frozen baseline evidence, the evaluated candidate evidence, the exact
candidate-minus-baseline comparison, the evidence-quality classification, the
human confirmations/decisions when they exist, and explicit limitations. It
never displays a candidate as adopted or production merely because its
historical metrics improved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.evidence import compose_id
from smcsignal.analysis.improvement.findings import (
    EvidenceQuality,
    ResearchFinding,
    classify_evidence,
)
from smcsignal.analysis.improvement.hypotheses import HypothesisConfirmation
from smcsignal.analysis.improvement.models import (
    CandidateExperimentDef,
    CandidateStatus,
    HumanDecisionRecord,
    Hypothesis,
)
from smcsignal.analysis.improvement.results import (
    CandidateComparisonReport,
    CandidateEvidence,
    EvaluationStatus,
)

_REVIEW_LIMITATIONS = (
    "historical evidence is descriptive and implies no guarantee of future performance.",
    "no causal inference is made from any observed relationship.",
    "no statistical significance is claimed unless it was separately established.",
    "small samples are inconclusive and are never treated as improvement evidence.",
    "the candidate has not been adopted and is not in production.",
    "better historical numbers are evidence only, not a selection or a recommendation.",
)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def review_identity(
    *,
    hypothesis_id: str,
    candidate_run_id: str | None,
    comparison_id: str | None,
) -> str:
    """Deterministic review identity from the referenced records."""
    return compose_id(
        "review",
        {
            "methodology": "improvement-v1",
            "kind": "research-review-report",
            "hypothesis_id": hypothesis_id,
            "candidate_run_id": candidate_run_id,
            "comparison_id": comparison_id,
        },
    )


@dataclass(frozen=True, slots=True)
class ResearchReviewReport:
    """One deterministic research-review report over a hypothesis and candidate."""

    review_id: str
    hypothesis: Hypothesis
    findings: tuple[ResearchFinding, ...]
    baseline: CandidateEvidence | None
    candidate: CandidateExperimentDef | None
    candidate_evidence: CandidateEvidence | None
    comparison: CandidateComparisonReport | None
    hypothesis_confirmation: HypothesisConfirmation | None
    human_candidate_decision: HumanDecisionRecord | None
    evidence_quality: EvidenceQuality
    quality_reason: str
    limitations: tuple[str, ...] = field(default_factory=lambda: _REVIEW_LIMITATIONS)

    def __post_init__(self) -> None:
        if not isinstance(self.review_id, str) or not self.review_id.startswith("review:"):
            raise AnalysisInputError("review_id must carry the review: prefix")
        if not isinstance(self.hypothesis, Hypothesis):
            raise AnalysisInputError("review requires a Hypothesis")
        if not isinstance(self.findings, tuple) or not all(
            isinstance(finding, ResearchFinding) for finding in self.findings
        ):
            raise AnalysisInputError("findings must be ResearchFinding records")
        if self.baseline is not None and not isinstance(self.baseline, CandidateEvidence):
            raise AnalysisInputError("baseline must be CandidateEvidence or None")
        if self.candidate is not None and not isinstance(self.candidate, CandidateExperimentDef):
            raise AnalysisInputError("candidate must be CandidateExperimentDef or None")
        if self.candidate_evidence is not None and not isinstance(
            self.candidate_evidence, CandidateEvidence
        ):
            raise AnalysisInputError("candidate_evidence must be CandidateEvidence or None")
        if self.comparison is not None and not isinstance(
            self.comparison, CandidateComparisonReport
        ):
            raise AnalysisInputError("comparison must be CandidateComparisonReport or None")
        if self.hypothesis_confirmation is not None and not isinstance(
            self.hypothesis_confirmation, HypothesisConfirmation
        ):
            raise AnalysisInputError(
                "hypothesis_confirmation must be HypothesisConfirmation or None"
            )
        if self.human_candidate_decision is not None and not isinstance(
            self.human_candidate_decision, HumanDecisionRecord
        ):
            raise AnalysisInputError("human_candidate_decision must be HumanDecisionRecord or None")
        if not isinstance(self.evidence_quality, EvidenceQuality):
            raise AnalysisInputError("evidence_quality must be an EvidenceQuality")
        _text(self.quality_reason, "quality_reason")
        if not self.limitations or not all(
            isinstance(limitation, str) and limitation.strip() for limitation in self.limitations
        ):
            raise AnalysisInputError("limitations must be nonempty strings")

    @property
    def display_decision(self) -> str:
        """REVIEW_REQUIRED unless an explicit Phase 23A human decision exists."""
        decision = self.human_candidate_decision
        if decision is not None:
            return decision.decision.value
        return CandidateStatus.REVIEW_REQUIRED.value


def build_review_report(
    *,
    hypothesis: Hypothesis,
    findings: tuple[ResearchFinding, ...],
    baseline: CandidateEvidence | None,
    candidate: CandidateExperimentDef | None,
    candidate_evidence: CandidateEvidence | None,
    comparison: CandidateComparisonReport | None,
    hypothesis_confirmation: HypothesisConfirmation | None,
    human_candidate_decision: HumanDecisionRecord | None,
) -> ResearchReviewReport:
    """Compose and classify one research-review report.

    Evidence quality is derived from the finalized samples and completeness of
    the baseline and candidate evaluations; the report is honest about
    insuffience and never treats a small or incomplete sample as improvement.
    """

    if not isinstance(hypothesis, Hypothesis):
        raise AnalysisInputError("build_review_report requires a Hypothesis")
    # A failed evaluation is a distinct, terminal evidence state: no comparable
    # evidence was produced, so the review cannot report a sample shortfall or
    # an incomplete run — it reports the failure itself.
    if candidate_evidence is not None and candidate_evidence.status is EvaluationStatus.FAILED:
        quality = EvidenceQuality.FAILED
        reason = "the candidate evaluation failed; no comparable evidence was produced"
    else:
        baseline_ok = baseline is not None
        completed = candidate_evidence is not None and comparison is not None
        finalized = candidate_evidence.overall_finalized if candidate_evidence is not None else 0
        minimum = comparison.minimum_finalized_for_comparison if comparison is not None else 30
        quality, reason = classify_evidence(
            finalized_count=finalized,
            minimum_finalized=minimum,
            validation_completed=completed,
            missing_windows=0,
            baseline_available=baseline_ok,
        )
        if not baseline_ok:
            quality, reason = (
                EvidenceQuality.INCOMPLETE,
                "no frozen baseline evidence is available",
            )
    review_id = review_identity(
        hypothesis_id=hypothesis.hypothesis_id,
        candidate_run_id=candidate_evidence.run_id if candidate_evidence else None,
        comparison_id=comparison.comparison_id if comparison else None,
    )
    return ResearchReviewReport(
        review_id=review_id,
        hypothesis=hypothesis,
        findings=findings,
        baseline=baseline,
        candidate=candidate,
        candidate_evidence=candidate_evidence,
        comparison=comparison,
        hypothesis_confirmation=hypothesis_confirmation,
        human_candidate_decision=human_candidate_decision,
        evidence_quality=quality,
        quality_reason=reason,
    )


def _fmt(value: Decimal | None) -> str:
    return "-" if value is None else format(value, "f")


def review_json(report: ResearchReviewReport) -> dict[str, object]:
    """Return a deterministic, JSON-serializable review report (Decimals as text)."""

    if not isinstance(report, ResearchReviewReport):
        raise AnalysisInputError("review_json requires a ResearchReviewReport")
    comparison_rows: list[dict[str, object]] = []
    if report.comparison is not None:
        for row in sorted(report.comparison.rows, key=lambda r: (r.dimension, r.group, r.metric)):
            comparison_rows.append(
                {
                    "dimension": row.dimension,
                    "group": row.group,
                    "metric": row.metric,
                    "candidate": _fmt(row.candidate_value),
                    "baseline": _fmt(row.baseline_value),
                    "difference_candidate_minus_baseline": _fmt(row.difference),
                    "conclusive": row.conclusive,
                }
            )
    return {
        "methodology": "improvement-v1",
        "kind": "research-review-report",
        "review_id": report.review_id,
        "decision": report.display_decision,
        "hypothesis": {
            "hypothesis_id": report.hypothesis.hypothesis_id,
            "observed_weakness": report.hypothesis.observed_weakness,
            "proposed_change": report.hypothesis.proposed_change,
            "expected_effect": report.hypothesis.expected_effect,
            "scope": report.hypothesis.scope,
            "status": report.hypothesis.status.value,
        },
        "confirmation": (
            None
            if report.hypothesis_confirmation is None
            else {
                "confirmation_id": report.hypothesis_confirmation.confirmation_id,
                "decision": report.hypothesis_confirmation.decision.value,
                "operator_identity": report.hypothesis_confirmation.operator_identity,
            }
        ),
        "baseline": (
            None
            if report.baseline is None
            else {
                "baseline_hash": report.baseline.baseline_hash,
                "run_id": report.baseline.run_id,
            }
        ),
        "candidate": (
            None
            if report.candidate is None
            else {
                "candidate_id": report.candidate.candidate_id,
                "status": report.candidate.status.value,
                "baseline_hash": report.candidate.baseline_hash,
            }
        ),
        "evidence_quality": report.evidence_quality.value,
        "quality_reason": report.quality_reason,
        "comparison_rows": comparison_rows,
        "findings": [
            {
                "finding_id": finding.finding_id,
                "dimension": finding.dimension,
                "group": finding.group,
                "metric": finding.metric,
                "value": _fmt(finding.value),
            }
            for finding in report.findings
        ],
        "limitations": list(report.limitations),
    }


def review_text(report: ResearchReviewReport) -> str:
    """Return a deterministic human-readable review report."""

    if not isinstance(report, ResearchReviewReport):
        raise AnalysisInputError("review_text requires a ResearchReviewReport")
    lines: list[str] = []
    lines.append("Phase 23C research-review report (research only; nothing adopted)")
    lines.append(f"review id   : {report.review_id}")
    lines.append(f"decision    : {report.display_decision}")
    lines.append("")
    lines.append("Hypothesis")
    lines.append(f"  id          : {report.hypothesis.hypothesis_id}")
    lines.append(f"  status      : {report.hypothesis.status.value}")
    lines.append(f"  observation : {report.hypothesis.observed_weakness}")
    lines.append(f"  change      : {report.hypothesis.proposed_change}")
    lines.append(f"  effect      : {report.hypothesis.expected_effect}")
    lines.append(f"  scope       : {report.hypothesis.scope}")
    if report.hypothesis_confirmation is not None:
        c = report.hypothesis_confirmation
        lines.append(
            f"  confirmation: {c.decision.value} by {c.operator_identity} ({c.confirmation_id})"
        )
    lines.append("")
    lines.append("Baseline")
    if report.baseline is None:
        lines.append("  (none)")
    else:
        lines.append(f"  baseline hash: {report.baseline.baseline_hash}")
        lines.append(f"  run id       : {report.baseline.run_id}")
        lines.append(f"  finalized    : {report.baseline.overall_finalized}")
    lines.append("")
    lines.append("Candidate")
    if report.candidate is None:
        lines.append("  (none)")
    else:
        lines.append(f"  candidate id : {report.candidate.candidate_id}")
        lines.append(f"  status       : {report.candidate.status.value}")
        for delta in report.candidate.deltas:
            lines.append(f"  delta        : {delta.key} {delta.old_value} -> {delta.new_value}")
    lines.append("")
    lines.append("Evidence quality")
    lines.append(f"  quality : {report.evidence_quality.value}")
    lines.append(f"  reason  : {report.quality_reason}")
    lines.append("")
    lines.append("Comparison (candidate - baseline; exact Decimal)")
    if report.comparison is None:
        lines.append("  (none)")
    else:
        for row in sorted(report.comparison.rows, key=lambda r: (r.dimension, r.group, r.metric)):
            lines.append(
                f"  {row.dimension}/{row.group}.{row.metric}: "
                f"{_fmt(row.difference)} "
                f"(candidate={_fmt(row.candidate_value)} baseline={_fmt(row.baseline_value)})"
            )
    lines.append("")
    lines.append("Findings (observations only)")
    for finding in report.findings:
        lines.append(f"  - {finding.finding_id}: {finding.observation}")
    lines.append("")
    lines.append("Limitations")
    for limitation in report.limitations:
        lines.append(f"  - {limitation}")
    return "\n".join(lines)
