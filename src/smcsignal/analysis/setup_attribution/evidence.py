"""Deterministic attribution identities and provenance over existing signal frames."""

from __future__ import annotations

from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest, provenance
from smcsignal.analysis.provenance import EvidenceProvenance
from smcsignal.analysis.setup_attribution.config import (
    METHODOLOGY_VERSION,
    SetupAttributionConfig,
)
from smcsignal.analysis.setup_attribution.models import (
    SetupAttribution,
    SetupLabel,
    current_observation,
)
from smcsignal.analysis.signal_engine.models import SignalSnapshot


def configuration_artifact(config: SetupAttributionConfig) -> bytes:
    """Frozen methodology artifact; the fact-only role is declared explicitly."""

    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "source": "existing_nested_facts_only",
            "outcome_dependence": False,
            "future_data": False,
            "strategy_invention": False,
            "mss_breaker_mitigation": "not_reachable_in_v1",
            "execution": False,
        }
    )


def attribution_identity(
    frame: SignalSnapshot, labels: tuple[SetupLabel, ...], settings: SetupAttributionConfig
) -> str:
    """Stable identity from the signal, its labels, and the configuration."""

    payload = {
        "methodology": METHODOLOGY_VERSION,
        "signal_id": frame.signal_id,
        "labels": labels,
        "enabled": settings.enabled,
    }
    return f"setup-attribution:{digest(payload)}"


def record_provenance(
    frame: SignalSnapshot,
    labels: tuple[SetupLabel, ...],
    settings: SetupAttributionConfig,
    config_hash: str,
) -> EvidenceProvenance:
    """One immutable attribution-record evidence record at the signal cutoff."""

    observation = current_observation(frame)
    return provenance(
        series=frame.provenance.series,
        producer="attribution-record",
        configuration_hash=config_hash,
        input_prefix_hash=frame.provenance.input_prefix_hash,
        available_at=observation.available_at,
        key={
            "signal_id": frame.signal_id,
            "setup_identity": frame.setup_identity,
            "labels": labels,
            "combination_key": "+".join(label.value for label in labels),
            "score_total": frame.candidate.signal.score_total,
            "component_scores": frame.upstream.upstream.score.breakdown,
            "symbol": observation.reference.series.symbol,
            "timeframe": observation.reference.series.timeframe,
        },
        source_candles=(observation.reference,),
        dependencies=(frame.provenance.as_reference(),),
    )


def snapshot_provenance(
    frame: SignalSnapshot,
    attribution: SetupAttribution | None,
    config_hash: str,
) -> EvidenceProvenance:
    """Per-frame attribution evidence: upstream signal plus the record."""

    observation = current_observation(frame)
    dependencies = [frame.provenance.as_reference()]
    if attribution is not None:
        dependencies.append(attribution.provenance.as_reference())
    return provenance(
        series=frame.provenance.series,
        producer="attribution-frame",
        configuration_hash=config_hash,
        input_prefix_hash=frame.provenance.input_prefix_hash,
        available_at=observation.available_at,
        key={
            "upstream": frame.provenance.evidence_id,
            "attribution": (None if attribution is None else attribution.provenance.evidence_id),
        },
        source_candles=(observation.reference,),
        dependencies=tuple(dependencies),
    )
