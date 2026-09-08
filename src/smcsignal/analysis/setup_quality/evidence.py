"""Setup-quality identities using the existing canonical/provenance contract."""

from smcsignal.analysis.halal_filter.models import HalalSnapshot
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.provenance import EvidenceProvenance
from smcsignal.analysis.setup_quality.config import METHODOLOGY_VERSION, SetupQualityConfig
from smcsignal.analysis.setup_quality.models import (
    WEIGHTS,
    ScoreBreakdown,
    ScoreComponent,
    SetupQualityScore,
    current_observation,
)


def configuration_artifact(config: SetupQualityConfig) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "weights": {component.value: WEIGHTS[component] for component in ScoreComponent},
            "gate": "non_halal_forces_zero",
            "missing": "zero_points",
            "signal": False,
            "probability": False,
            "optimization": False,
        }
    )


def score_provenance(
    upstream: HalalSnapshot,
    breakdown: ScoreBreakdown,
    eligible: bool,
    settings: SetupQualityConfig,
    config_hash: str,
) -> EvidenceProvenance:
    observation = current_observation(upstream)
    total = breakdown.total
    threshold_passed = eligible and total >= settings.publish_threshold
    return provenance(
        series=upstream.provenance.series,
        producer="sqs-score",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=observation.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "eligible": eligible,
            "total": total,
            "threshold_passed": threshold_passed,
            "contributions": {
                component.value: getattr(breakdown, component.value) for component in ScoreComponent
            },
            "reasons": breakdown.reasons,
        },
        source_candles=(observation.reference,),
        dependencies=(upstream.provenance.as_reference(),),
    )


def snapshot_provenance(
    upstream: HalalSnapshot, score: SetupQualityScore, config_hash: str
) -> EvidenceProvenance:
    observation = current_observation(upstream)
    return provenance(
        series=upstream.provenance.series,
        producer="sqs-frame",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=upstream.provenance.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "score": score.provenance.evidence_id,
        },
        source_candles=(observation.reference,),
        dependencies=(
            upstream.provenance.as_reference(),
            score.provenance.as_reference(),
        ),
    )
