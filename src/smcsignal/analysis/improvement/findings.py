"""Deterministic research findings and evidence-quality classification.

A finding is a descriptive observation of an already-published historical
pattern (from the Phase 22 strategy-intelligence report or a Phase 21
robustness summary). Findings are observations only: they never instruct,
recommend, trade, or claim causation, significance, or future predictive
power. Evidence-quality classification gates whether a pattern may be used to
form a hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.evidence import compose_id
from smcsignal.analysis.intelligence.models import (
    DiagnosticLabel,
    IntelligenceCell,
    IntelligenceReport,
)


class EvidenceQuality(StrEnum):
    """Deterministic evidence-quality classification of a research comparison."""

    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class FindingKind(StrEnum):
    """The descriptive kind of an observed historical pattern."""

    LOSER_CELL = "LOSER_CELL"
    WEAKNESS_CELL = "WEAKNESS_CELL"
    PERSISTENT_LOW_WIN_RATE = "PERSISTENT_LOW_WIN_RATE"
    NEGATIVE_AVERAGE_RETURN = "NEGATIVE_AVERAGE_RETURN"
    REPEATED_DEGRADATION = "REPEATED_DEGRADATION"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def classify_evidence(
    *,
    finalized_count: int,
    minimum_finalized: int,
    validation_completed: bool,
    missing_windows: int,
    baseline_available: bool,
) -> tuple[EvidenceQuality, str]:
    """Classify research evidence quality for one comparison.

    A comparison is only SUFFICIENT when the experiment completed, every
    validation window is present, a baseline is available, and the finalized
    outcome sample reaches ``minimum_finalized``. Anything else is never
    treated as evidence of improvement; the raw numbers are preserved by the
    caller.
    """

    if type(finalized_count) is not int or finalized_count < 0:
        raise AnalysisInputError("finalized_count must be a nonnegative integer")
    if type(minimum_finalized) is not int or minimum_finalized < 1:
        raise AnalysisInputError("minimum_finalized must be a positive integer")
    if type(missing_windows) is not int or missing_windows < 0:
        raise AnalysisInputError("missing_windows must be a nonnegative integer")
    if type(validation_completed) is not bool or type(baseline_available) is not bool:
        raise AnalysisInputError("validation_completed and baseline_available must be booleans")
    if not baseline_available:
        return EvidenceQuality.INCOMPLETE, "no frozen baseline evidence is available"
    if not validation_completed or missing_windows > 0:
        reason = "experiment is incomplete"
        if missing_windows > 0:
            reason += f" ({missing_windows} validation window(s) missing)"
        return EvidenceQuality.INCOMPLETE, reason
    if finalized_count < minimum_finalized:
        return (
            EvidenceQuality.INSUFFICIENT_SAMPLE,
            f"finalized sample {finalized_count} is below the {minimum_finalized} minimum",
        )
    return EvidenceQuality.SUFFICIENT, f"finalized sample {finalized_count} meets the minimum"


def _observation_for_cell(cell: IntelligenceCell) -> str:
    scope = _cell_scope(cell)
    value = cell.win_rate
    win_text = "n/a" if value is None else format(value, "f")
    return (
        f"{cell.dimension.value} group {cell.name!r} had validation win_rate {win_text} "
        f"on {cell.finalized_count} finalized signal(s) ({scope}); descriptive observation only"
    )


def _cell_scope(cell: IntelligenceCell) -> str:
    if cell.dimension.value == "overall":
        return "overall population"
    return f"scope={cell.dimension.value}:{cell.name}"


@dataclass(frozen=True, slots=True)
class ResearchFinding:
    """One descriptive, deterministic observation of published historical evidence."""

    finding_id: str
    origin_evidence_ids: tuple[str, ...]
    evidence_quality: EvidenceQuality
    kind: FindingKind
    dimension: str
    group: str
    metric: str
    value: Decimal | None
    sample_finalized: int
    observation: str
    scope: str

    def __post_init__(self) -> None:
        if not isinstance(self.finding_id, str) or not self.finding_id.startswith("finding:"):
            raise AnalysisInputError("finding_id must carry the finding: prefix")
        if not self.origin_evidence_ids or not all(
            isinstance(ref, str) and ref.strip() for ref in self.origin_evidence_ids
        ):
            raise AnalysisInputError("origin_evidence_ids must be a nonempty tuple of strings")
        if not isinstance(self.evidence_quality, EvidenceQuality):
            raise AnalysisInputError("evidence_quality must be an EvidenceQuality")
        if not isinstance(self.kind, FindingKind):
            raise AnalysisInputError("kind must be a FindingKind")
        for label, value in (
            ("dimension", self.dimension),
            ("group", self.group),
            ("metric", self.metric),
            ("observation", self.observation),
            ("scope", self.scope),
        ):
            _text(value, label)
        if self.value is not None and (
            not isinstance(self.value, Decimal) or not self.value.is_finite()
        ):
            raise AnalysisInputError("value must be a finite Decimal or None")
        if type(self.sample_finalized) is not int or self.sample_finalized < 0:
            raise AnalysisInputError("sample_finalized must be a nonnegative integer")


def finding_identity(
    *,
    origin_evidence_ids: tuple[str, ...],
    evidence_quality: EvidenceQuality,
    kind: FindingKind,
    dimension: str,
    group: str,
    metric: str,
    observation: str,
) -> str:
    """Deterministic finding identity over its descriptive, published inputs."""
    return compose_id(
        "finding",
        {
            "methodology": "improvement-v1",
            "kind": "research-finding",
            "origin_evidence_ids": sorted(origin_evidence_ids),
            "evidence_quality": evidence_quality.value,
            "finding_kind": kind.value,
            "dimension": dimension,
            "group": group,
            "metric": metric,
            "observation": observation,
        },
    )


def finding_from_cell(
    cell: IntelligenceCell,
    *,
    minimum_finalized: int,
    origin_evidence_ids: tuple[str, ...],
) -> ResearchFinding | None:
    """Describe one intelligence cell only if it is a SUFFICIENT weakness/loser.

    A cell whose finalized sample is below ``minimum_finalized`` is not treated
    as a weakness finding (it is insufficient). Returns ``None`` otherwise, so
    the caller can record insuffience separately without fabricating a pattern.
    """

    if not isinstance(cell, IntelligenceCell):
        raise AnalysisInputError("finding_from_cell requires an IntelligenceCell")
    quality, _reason = classify_evidence(
        finalized_count=cell.finalized_count,
        minimum_finalized=minimum_finalized,
        validation_completed=True,
        missing_windows=0,
        baseline_available=True,
    )
    is_weak = cell.diagnostic is DiagnosticLabel.WEAKNESS or cell.pattern.value == "LOSER"
    if quality is not EvidenceQuality.SUFFICIENT or not is_weak:
        return None
    kind: FindingKind
    if cell.diagnostic is DiagnosticLabel.WEAKNESS:
        kind = FindingKind.WEAKNESS_CELL
    else:
        kind = FindingKind.LOSER_CELL
    observation = _observation_for_cell(cell)
    finding_id = finding_identity(
        origin_evidence_ids=origin_evidence_ids,
        evidence_quality=quality,
        kind=kind,
        dimension=cell.dimension.value,
        group=cell.name,
        metric="win_rate",
        observation=observation,
    )
    return ResearchFinding(
        finding_id=finding_id,
        origin_evidence_ids=origin_evidence_ids,
        evidence_quality=quality,
        kind=kind,
        dimension=cell.dimension.value,
        group=cell.name,
        metric="win_rate",
        value=cell.win_rate,
        sample_finalized=cell.finalized_count,
        observation=observation,
        scope=_cell_scope(cell),
    )


def derive_findings(
    report: IntelligenceReport, *, minimum_finalized: int
) -> tuple[ResearchFinding, ...]:
    """Derive descriptive weakness findings from the intelligence report cells.

    Every sufficiency-gated weakness/loser cell across overall, setup, symbol,
    timeframe, month, and regime becomes one finding. The origin evidence id is
    the report id plus the cell key, and no future or post-outcome value may
    alter what is described here.
    """

    if not isinstance(report, IntelligenceReport):
        raise AnalysisInputError("derive_findings requires an IntelligenceReport")
    cells = [report.overall]
    for family in (
        report.by_setup,
        report.by_symbol,
        report.by_timeframe,
        report.by_month,
        report.by_regime,
    ):
        cells.extend(family)
    findings: list[ResearchFinding] = []
    for cell in cells:
        origin = (f"{report.report_id}#cell:{cell.dimension.value}:{cell.name}",)
        finding = finding_from_cell(
            cell, minimum_finalized=minimum_finalized, origin_evidence_ids=origin
        )
        if finding is not None:
            findings.append(finding)
    # deterministic ordering regardless of input cell ordering
    return tuple(sorted(findings, key=lambda finding: finding.finding_id))
