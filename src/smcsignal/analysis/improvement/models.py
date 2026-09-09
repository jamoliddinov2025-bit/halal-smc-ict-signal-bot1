"""Frozen Phase 23 proposal, candidate, and human-decision models.

Every record is immutable and carries a deterministic identity derived only
from canonical frozen inputs. A hypothesis is a proposal only. A candidate is
``frozen baseline + explicit declared deltas`` and never mutates a baseline
configuration. A human decision is an explicit, operator-attributed record;
no Phase 23 code path may produce an APPROVED or REJECTED status on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement import evidence
from smcsignal.analysis.improvement.surfaces import lookup_surface, validate_new_value


class CandidateStatus(StrEnum):
    """The controlled candidate lifecycle; APPROVED/REJECTED are human-only."""

    PROPOSED = "PROPOSED"
    EXPERIMENTAL = "EXPERIMENTAL"
    EVALUATED = "EVALUATED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class HypothesisStatus(StrEnum):
    """Proposal-only status; a hypothesis never changes the production strategy."""

    PROPOSED = "PROPOSED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def _id_prefix(value: str, prefix: str, name: str) -> str:
    _text(value, name)
    if not value.startswith(f"{prefix}:"):
        raise AnalysisInputError(f"{name} must use the {prefix} prefix")
    return value


@dataclass(frozen=True, slots=True)
class CandidateDelta:
    """One declared change to a single allow-listed frozen setting.

    ``old_value`` must equal the frozen baseline value of the surface (so an
    invalid old-value declaration is rejected) and ``new_value`` must be a
    distinct value valid under that surface's contract. A delta is never
    applied to a baseline in place; it is a frozen declaration only.
    """

    component: str
    setting: str
    old_value: int | Decimal
    new_value: int | Decimal

    def __post_init__(self) -> None:
        component = _text(self.component, "delta component")
        setting = _text(self.setting, "delta setting")
        object.__setattr__(self, "component", component)
        object.__setattr__(self, "setting", setting)
        surface = lookup_surface(component, setting)
        baseline = surface.baseline_value
        if self.old_value != baseline:
            raise AnalysisInputError(
                f"{surface.key} old_value must equal the frozen baseline value {baseline!r}"
            )
        validate_new_value(self.new_value, surface)
        if self.new_value == self.old_value:
            raise AnalysisInputError(f"{surface.key} delta requires a changed new_value")

    @property
    def key(self) -> str:
        return f"{self.component}.{self.setting}"


def _validate_deltas(deltas: tuple[CandidateDelta, ...]) -> tuple[CandidateDelta, ...]:
    if not isinstance(deltas, tuple) or not all(
        isinstance(delta, CandidateDelta) for delta in deltas
    ):
        raise AnalysisInputError("candidate deltas must be a tuple of CandidateDelta records")
    if not deltas:
        raise AnalysisInputError("a candidate must declare at least one delta")
    ordered = tuple(sorted(deltas, key=lambda delta: delta.key))
    if len({delta.key for delta in ordered}) != len(ordered):
        raise AnalysisInputError("a candidate may not contain two deltas for the same surface")
    return ordered


@dataclass(frozen=True, slots=True)
class CandidateExperimentDef:
    """One immutable candidate: a frozen baseline plus explicit deltas.

    The candidate never holds or mutates a baseline configuration; it stores
    the canonical ``baseline_hash`` reference and the declared deltas. Its
    ``candidate_id`` is a deterministic digest of the baseline hash and the
    sorted deltas, independent of prose and of the approval status.
    """

    baseline_hash: str
    deltas: tuple[CandidateDelta, ...]
    rationale: str
    expected_effect: str
    candidate_id: str
    status: CandidateStatus = CandidateStatus.PROPOSED

    def __post_init__(self) -> None:
        evidence.check_baseline_hash(self.baseline_hash)
        object.__setattr__(self, "deltas", _validate_deltas(self.deltas))
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale"))
        object.__setattr__(self, "expected_effect", _text(self.expected_effect, "expected_effect"))
        _id_prefix(self.candidate_id, "candidate", "candidate_id")
        if not isinstance(self.status, CandidateStatus):
            raise AnalysisInputError("candidate status must be a CandidateStatus")
        expected = candidate_identity(self.baseline_hash, self.deltas)
        if self.candidate_id != expected:
            raise AnalysisInputError("candidate_id must match the deterministic identity")


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """One improvement proposal; it changes nothing and runs nothing."""

    hypothesis_id: str
    title: str
    affected_component: str
    observed_weakness: str
    proposed_change: str
    rationale: str
    expected_effect: str
    scope: str
    baseline_hash: str | None
    evidence_references: tuple[str, ...]
    candidate_reference: str | None
    status: HypothesisStatus = HypothesisStatus.PROPOSED

    def __post_init__(self) -> None:
        _id_prefix(self.hypothesis_id, "hypothesis", "hypothesis_id")
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(
            self, "affected_component", _text(self.affected_component, "affected_component")
        )
        object.__setattr__(
            self, "observed_weakness", _text(self.observed_weakness, "observed_weakness")
        )
        object.__setattr__(self, "proposed_change", _text(self.proposed_change, "proposed_change"))
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale"))
        object.__setattr__(self, "expected_effect", _text(self.expected_effect, "expected_effect"))
        object.__setattr__(self, "scope", _text(self.scope, "scope"))
        if self.baseline_hash is not None:
            evidence.check_baseline_hash(self.baseline_hash)
        if not isinstance(self.evidence_references, tuple) or not all(
            isinstance(ref, str) and ref.strip() for ref in self.evidence_references
        ):
            raise AnalysisInputError("evidence_references must be a tuple of trimmed strings")
        if self.candidate_reference is not None:
            _id_prefix(self.candidate_reference, "candidate", "candidate_reference")
        if not isinstance(self.status, HypothesisStatus):
            raise AnalysisInputError("hypothesis status must be a HypothesisStatus")
        if self.status is not HypothesisStatus.PROPOSED:
            raise AnalysisInputError("a hypothesis is always created in the PROPOSED state")
        expected = hypothesis_identity(
            title=self.title,
            affected_component=self.affected_component,
            observed_weakness=self.observed_weakness,
            proposed_change=self.proposed_change,
            baseline_hash=self.baseline_hash,
            evidence_references=self.evidence_references,
        )
        if self.hypothesis_id != expected:
            raise AnalysisInputError("hypothesis_id must match the deterministic identity")


@dataclass(frozen=True, slots=True)
class HumanDecisionRecord:
    """One explicit human approval/rejection decision with operator provenance.

    Operator identity is supplied explicitly by the caller and is never
    inferred or invented. ``evidence_id`` references the evaluation/comparison
    report consulted, where applicable.
    """

    decision_id: str
    candidate_id: str
    decision: CandidateStatus
    operator_identity: str
    rationale: str
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        _id_prefix(self.decision_id, "decision", "decision_id")
        _id_prefix(self.candidate_id, "candidate", "candidate_id")
        if self.decision not in (CandidateStatus.APPROVED, CandidateStatus.REJECTED):
            raise AnalysisInputError("a human decision must be APPROVED or REJECTED")
        object.__setattr__(
            self, "operator_identity", _text(self.operator_identity, "operator_identity")
        )
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale"))
        if self.evidence_id is not None:
            _text(self.evidence_id, "evidence_id")
        expected = decision_identity(
            candidate_id=self.candidate_id,
            decision=self.decision,
            operator_identity=self.operator_identity,
            rationale=self.rationale,
            evidence_id=self.evidence_id,
        )
        if self.decision_id != expected:
            raise AnalysisInputError("decision_id must match the deterministic identity")


def candidate_identity(baseline_hash: str, deltas: tuple[CandidateDelta, ...]) -> str:
    """Deterministic candidate identity from the baseline and sorted deltas."""
    evidence.check_baseline_hash(baseline_hash)
    ordered = sorted(deltas, key=lambda delta: delta.key)
    return evidence.compose_id(
        "candidate",
        {
            "methodology": "improvement-v1",
            "kind": "candidate-experiment",
            "baseline_hash": baseline_hash,
            "deltas": [
                {
                    "component": delta.component,
                    "setting": delta.setting,
                    "old_value": delta.old_value,
                    "new_value": delta.new_value,
                }
                for delta in ordered
            ],
        },
    )


def hypothesis_identity(
    *,
    title: str,
    affected_component: str,
    observed_weakness: str,
    proposed_change: str,
    baseline_hash: str | None,
    evidence_references: tuple[str, ...] = (),
) -> str:
    """Deterministic hypothesis identity (re-export for public construction)."""
    return evidence.hypothesis_identity(
        title=title,
        affected_component=affected_component,
        observed_weakness=observed_weakness,
        proposed_change=proposed_change,
        baseline_hash=baseline_hash,
        evidence_references=evidence_references,
    )


def decision_identity(
    *,
    candidate_id: str,
    decision: CandidateStatus,
    operator_identity: str,
    rationale: str,
    evidence_id: str | None = None,
) -> str:
    """Deterministic human-decision identity (re-export for public construction)."""
    return evidence.decision_identity(
        candidate_id=candidate_id,
        decision=decision.value,
        operator_identity=operator_identity,
        rationale=rationale,
        evidence_id=evidence_id,
    )


def propose_delta(component: str, setting: str, new_value: int | Decimal) -> CandidateDelta:
    """Build a delta anchored to the frozen baseline value of its surface."""
    surface = lookup_surface(component, setting)
    return CandidateDelta(
        component=component,
        setting=setting,
        old_value=surface.baseline_value,
        new_value=new_value,
    )


def build_candidate(
    *,
    baseline_hash: str,
    deltas: tuple[CandidateDelta, ...],
    rationale: str,
    expected_effect: str,
) -> CandidateExperimentDef:
    """Construct a PROPOSED candidate with its deterministic identity."""
    return CandidateExperimentDef(
        baseline_hash=baseline_hash,
        deltas=deltas,
        rationale=rationale,
        expected_effect=expected_effect,
        candidate_id=candidate_identity(baseline_hash, deltas),
    )


def build_hypothesis(
    *,
    title: str,
    affected_component: str,
    observed_weakness: str,
    proposed_change: str,
    rationale: str,
    expected_effect: str,
    scope: str,
    baseline_hash: str | None,
    evidence_references: tuple[str, ...] = (),
    candidate_reference: str | None = None,
) -> Hypothesis:
    """Construct a PROPOSED hypothesis (a proposal only) with its identity."""
    return Hypothesis(
        hypothesis_id=hypothesis_identity(
            title=title,
            affected_component=affected_component,
            observed_weakness=observed_weakness,
            proposed_change=proposed_change,
            baseline_hash=baseline_hash,
            evidence_references=evidence_references,
        ),
        title=title,
        affected_component=affected_component,
        observed_weakness=observed_weakness,
        proposed_change=proposed_change,
        rationale=rationale,
        expected_effect=expected_effect,
        scope=scope,
        baseline_hash=baseline_hash,
        evidence_references=evidence_references,
        candidate_reference=candidate_reference,
    )


def build_decision(
    *,
    candidate: CandidateExperimentDef,
    decision: CandidateStatus,
    operator_identity: str,
    rationale: str,
    evidence_id: str | None = None,
) -> HumanDecisionRecord:
    """Construct an explicit human decision record for a candidate."""
    return HumanDecisionRecord(
        decision_id=decision_identity(
            candidate_id=candidate.candidate_id,
            decision=decision,
            operator_identity=operator_identity,
            rationale=rationale,
            evidence_id=evidence_id,
        ),
        candidate_id=candidate.candidate_id,
        decision=decision,
        operator_identity=operator_identity,
        rationale=rationale,
        evidence_id=evidence_id,
    )
