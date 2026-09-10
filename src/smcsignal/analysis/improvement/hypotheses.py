"""Deterministic, evidence-driven hypothesis generation and human confirmation.

Hypothesis generation only turns already-published evidence into testable
proposals. It never sets APPROVED, never tunes a parameter, never selects a
new value from performance, never claims causation or significance, and never
silently starts an experiment. The explicit new value of a candidate delta is
always supplied by a human through the Phase 23A allow-list; the generator
only observes a descriptive weakness and frames a test of that declared
change. Human confirmation is recorded separately and is required before any
candidate may be created from a hypothesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.evidence import compose_id
from smcsignal.analysis.improvement.findings import ResearchFinding
from smcsignal.analysis.improvement.models import (
    CandidateDelta,
    Hypothesis,
    build_hypothesis,
)


class ConfirmationDecision(StrEnum):
    """A human's explicit decision on whether a hypothesis is worth testing."""

    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class HypothesisPattern(StrEnum):
    """The descriptive evidence pattern that motivated a hypothesis."""

    SUFFICIENT_WEAKNESS = "SUFFICIENT_WEAKNESS"


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Detection thresholds for descriptive evidence conditions only.

    These values only control when a descriptive weakness is treated as
    sufficient to frame a hypothesis. There is no tuning, selection,
    optimization, or adoption setting anywhere in the generator.
    """

    minimum_finalized_for_detection: int = 30

    def __post_init__(self) -> None:
        if (
            type(self.minimum_finalized_for_detection) is not int
            or self.minimum_finalized_for_detection < 1
        ):
            raise AnalysisInputError("minimum_finalized_for_detection must be a positive integer")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def _delta_label(delta: CandidateDelta) -> str:
    return f"{delta.component}.{delta.setting} from {delta.old_value} to {delta.new_value}"


def generate_hypotheses(
    *,
    delta: CandidateDelta,
    baseline_hash: str,
    findings: tuple[ResearchFinding, ...],
    config: GeneratorConfig | None = None,
) -> tuple[Hypothesis, ...]:
    """Frame deterministic, testable proposals from sufficient weakness findings.

    Each finding is a descriptive weakness already observed in published
    evidence. The generator does not pick ``delta.new_value`` (a human declared
    it); it only states an explicit test of that declared change against the
    observed weakness scope. Output hypotheses are always PROPOSED. If no
    finding reaches the configured detection minimum, no hypothesis is emitted.
    """

    if not isinstance(delta, CandidateDelta):
        raise AnalysisInputError("generate_hypotheses requires a CandidateDelta")
    if config is not None and not isinstance(config, GeneratorConfig):
        raise AnalysisInputError("config must be a GeneratorConfig")
    if not isinstance(findings, tuple) or not all(
        isinstance(finding, ResearchFinding) for finding in findings
    ):
        raise AnalysisInputError("findings must be a tuple of ResearchFinding records")
    settings = config if config is not None else GeneratorConfig()
    hypotheses: list[Hypothesis] = []
    for finding in findings:
        if finding.sample_finalized < settings.minimum_finalized_for_detection:
            continue
        scope = finding.scope
        component = delta.component
        proposed_change = (
            f"Test whether changing declared {_delta_label(delta)} changes "
            f"validation behavior for {scope}."
        )
        expected_effect = (
            f"Measure the candidate-minus-baseline validation {finding.metric} "
            f"difference within {scope}; no improvement is claimed unless "
            "sufficient evidence supports it."
        )
        hypotheses.append(
            build_hypothesis(
                title=f"Test declared {delta.component}.{delta.setting} change on {scope}",
                affected_component=component,
                observed_weakness=finding.observation,
                proposed_change=proposed_change,
                rationale=(
                    f"Descriptive weakness observed in {finding.dimension}/{finding.group} "
                    f"(finding {finding.finding_id}). This is an observation requiring testing."
                ),
                expected_effect=expected_effect,
                scope=scope,
                baseline_hash=baseline_hash,
                evidence_references=finding.origin_evidence_ids,
            )
        )
    return tuple(sorted(hypotheses, key=lambda hypothesis: hypothesis.hypothesis_id))


@dataclass(frozen=True, slots=True)
class HypothesisConfirmation:
    """An explicit human confirmation or rejection of a hypothesis.

    ``confirmed_at`` is a display/provenance timestamp and is deliberately
    excluded from the deterministic ``confirmation_id`` so that the identity
    never depends on wall-clock time.
    """

    confirmation_id: str
    hypothesis_id: str
    decision: ConfirmationDecision
    operator_identity: str
    rationale: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.confirmation_id, str) or not self.confirmation_id.startswith(
            "confirmation:"
        ):
            raise AnalysisInputError("confirmation_id must carry the confirmation: prefix")
        if not isinstance(self.hypothesis_id, str) or not self.hypothesis_id.startswith(
            "hypothesis:"
        ):
            raise AnalysisInputError("hypothesis_id must carry the hypothesis: prefix")
        if not isinstance(self.decision, ConfirmationDecision):
            raise AnalysisInputError("decision must be a ConfirmationDecision")
        for label, value in (
            ("operator_identity", self.operator_identity),
            ("rationale", self.rationale),
        ):
            _text(value, label)
        if not isinstance(self.confirmed_at, datetime):
            raise AnalysisInputError("confirmed_at must be a datetime")


def confirmation_identity(
    *,
    hypothesis_id: str,
    decision: ConfirmationDecision,
    operator_identity: str,
    rationale: str,
) -> str:
    """Deterministic confirmation identity; the timestamp is intentionally absent."""
    return compose_id(
        "confirmation",
        {
            "methodology": "improvement-v1",
            "kind": "hypothesis-confirmation",
            "hypothesis_id": hypothesis_id,
            "decision": decision.value,
            "operator_identity": operator_identity,
            "rationale": rationale,
        },
    )


def confirm_hypothesis(
    *,
    hypothesis: Hypothesis,
    decision: ConfirmationDecision,
    operator_identity: str,
    rationale: str,
    confirmed_at: datetime,
) -> HypothesisConfirmation:
    """Record one explicit human decision about a hypothesis.

    This is the only sanctioned way a hypothesis may be accepted for testing.
    The hypothesis record itself stays PROPOSED (a proposal); the confirmation
    is the gate that later permits candidate creation.
    """

    if not isinstance(hypothesis, Hypothesis):
        raise AnalysisInputError("confirm_hypothesis requires a Hypothesis")
    return HypothesisConfirmation(
        confirmation_id=confirmation_identity(
            hypothesis_id=hypothesis.hypothesis_id,
            decision=decision,
            operator_identity=operator_identity,
            rationale=rationale,
        ),
        hypothesis_id=hypothesis.hypothesis_id,
        decision=decision,
        operator_identity=operator_identity,
        rationale=rationale,
        confirmed_at=confirmed_at,
    )
