"""Breaker formation identities using the original canonical/provenance contract."""

from smcsignal.analysis.breaker_blocks.config import METHODOLOGY_VERSION, BreakerBlockConfig
from smcsignal.analysis.breaker_blocks.models import (
    BreakerBlock,
    BreakerEvidence,
    evidence_references,
    evidence_sources,
)
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.mss.models import MSSSnapshot
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.provenance import EvidenceProvenance


def configuration_artifact(config: BreakerBlockConfig, price_unit: str) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "price_unit": price_unit,
            "selection": "all_in_original_publication_order",
            "confirmation_window": "first_violation_candle_only",
        }
    )


def build_evidence(
    origin: OrderBlockEvent,
    previous: MSSSnapshot | None,
    current: MSSSnapshot,
    config: BreakerBlockConfig,
    config_hash: str,
) -> BreakerEvidence:
    return BreakerEvidence(
        config,
        origin,
        previous,
        current,
        provenance(
            series=current.provenance.series,
            producer="breaker-evidence",
            configuration_hash=config_hash,
            input_prefix_hash=current.provenance.input_prefix_hash,
            available_at=current.provenance.available_at,
            key={"original_ob": origin.event_id, "upstream": current.provenance.evidence_id},
            source_candles=evidence_sources(previous, current),
            dependencies=evidence_references(origin, previous, current),
        ),
    )


def build_block(evidence: BreakerEvidence) -> BreakerBlock:
    meta = evidence.provenance
    return BreakerBlock(
        evidence,
        provenance(
            series=meta.series,
            producer="breaker-block",
            configuration_hash=meta.configuration_hash,
            input_prefix_hash=meta.input_prefix_hash,
            available_at=meta.available_at,
            key={"evidence": meta.evidence_id, "original_ob": evidence.original_ob_id},
            source_candles=meta.source_candles,
            dependencies=(meta.as_reference(),),
        ),
    )


def snapshot_provenance(
    previous: MSSSnapshot | None,
    current: MSSSnapshot,
    evidence: tuple[BreakerEvidence, ...],
    events: tuple[BreakerBlock, ...],
    config_hash: str,
) -> EvidenceProvenance:
    refs = (current.provenance.as_reference(),) + (
        (previous.provenance.as_reference(),) if previous is not None else ()
    )
    refs += tuple(e.provenance.as_reference() for e in evidence) + tuple(
        e.provenance.as_reference() for e in events
    )
    return provenance(
        series=current.provenance.series,
        producer="breaker-frame",
        configuration_hash=config_hash,
        input_prefix_hash=current.provenance.input_prefix_hash,
        available_at=current.provenance.available_at,
        key={"upstream": current.provenance.evidence_id},
        source_candles=(current.upstream.observation.reference,),
        dependencies=refs,
    )
