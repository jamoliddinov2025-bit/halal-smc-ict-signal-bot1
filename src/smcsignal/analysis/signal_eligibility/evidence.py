"""Eligibility identities using the existing canonical/provenance contract."""

from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference
from smcsignal.analysis.setup_quality.models import ScoreSnapshot
from smcsignal.analysis.signal_eligibility.config import (
    METHODOLOGY_VERSION,
    SignalEligibilityConfig,
)
from smcsignal.analysis.signal_eligibility.models import (
    EligibilityDecision,
    EligibilityReason,
    SignalEligibility,
    current_observation,
)


def configuration_artifact(config: SignalEligibilityConfig) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "gate": "halal_and_threshold",
            "conflict": "neutral",
            "missing": "abstain",
            "buy": False,
            "sell": False,
            "entry": False,
            "optimization": False,
        }
    )


def decision_provenance(
    upstream: ScoreSnapshot,
    eligibility: SignalEligibility,
    reasons: tuple[EligibilityReason, ...],
    evidence: tuple[EvidenceReference, ...],
    config_hash: str,
) -> EvidenceProvenance:
    observation = current_observation(upstream)
    return provenance(
        series=upstream.provenance.series,
        producer="eligibility-decision",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=observation.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "status": eligibility.status,
            "bias": eligibility.bias,
            "classification": eligibility.classification,
            "score_total": eligibility.score_total,
            "threshold_passed": eligibility.threshold_passed,
            "publish_threshold": eligibility.publish_threshold,
            "reasons": reasons,
            "evidence": tuple(item.evidence_id for item in evidence),
        },
        source_candles=(observation.reference,),
        dependencies=(upstream.provenance.as_reference(),),
    )


def snapshot_provenance(
    upstream: ScoreSnapshot, decision: EligibilityDecision, config_hash: str
) -> EvidenceProvenance:
    observation = current_observation(upstream)
    return provenance(
        series=upstream.provenance.series,
        producer="eligibility-frame",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=upstream.provenance.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "decision": decision.provenance.evidence_id,
        },
        source_candles=(observation.reference,),
        dependencies=(
            upstream.provenance.as_reference(),
            decision.provenance.as_reference(),
        ),
    )
