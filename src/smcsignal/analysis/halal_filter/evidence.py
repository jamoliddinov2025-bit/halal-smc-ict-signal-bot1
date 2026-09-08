"""Halal-filter identities using the existing canonical/provenance contract."""

from datetime import datetime

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter.classification import classify_asset, decision_reason
from smcsignal.analysis.halal_filter.config import (
    METHODOLOGY_VERSION,
    HalalFilterConfig,
    normalize_asset,
)
from smcsignal.analysis.halal_filter.models import HalalDecision, current_observation
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest, provenance
from smcsignal.analysis.mtf.models import MTFSnapshot
from smcsignal.analysis.provenance import EvidenceProvenance, SeriesProvenance


def configuration_artifact(config: HalalFilterConfig) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "policy": "registry_enforcement_only",
            "unknown": "never_silently_halal",
            "internet": False,
            "autonomous_rulings": False,
        }
    )


def build_decision(
    config: HalalFilterConfig,
    symbol: str,
    series: SeriesProvenance,
    available_at: datetime,
    config_hash: str,
) -> HalalDecision:
    normalized = normalize_asset(symbol, error=AnalysisInputError)
    classification = classify_asset(normalized, config)
    reason = decision_reason(normalized, config)
    return HalalDecision(
        config,
        normalized,
        provenance(
            series=series,
            producer="halal-decision",
            configuration_hash=config_hash,
            input_prefix_hash=digest(
                {
                    "schema": "halal-registry-v1",
                    "methodology": METHODOLOGY_VERSION,
                    "series": series,
                    "symbol": normalized,
                }
            ),
            available_at=available_at,
            key={
                "symbol": normalized,
                "classification": classification,
                "mode": config.mode,
                "reason": reason,
            },
            source_candles=(),
            dependencies=(),
        ),
    )


def snapshot_provenance(
    upstream: MTFSnapshot, decision: HalalDecision, config_hash: str
) -> EvidenceProvenance:
    observation = current_observation(upstream)
    return provenance(
        series=upstream.provenance.series,
        producer="halal-frame",
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
