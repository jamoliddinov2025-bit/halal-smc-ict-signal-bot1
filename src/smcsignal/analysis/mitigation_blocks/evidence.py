"""Mitigation identities using the original canonical/provenance contract."""

from smcsignal.analysis.breaker_blocks.models import BreakerSnapshot
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.mitigation_blocks.calculation import observation
from smcsignal.analysis.mitigation_blocks.config import METHODOLOGY_VERSION, MitigationBlockConfig
from smcsignal.analysis.mitigation_blocks.models import (
    MitigationBlock,
    MitigationEvidence,
    evidence_references,
    evidence_sources,
)
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.provenance import EvidenceProvenance


def configuration_artifact(config: MitigationBlockConfig, price_unit: str) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "price_unit": price_unit,
            "selection": "all_in_original_publication_order",
            "interaction_window": "first_post_publication_overlap_only",
        }
    )


def build_evidence(
    origin: OrderBlockEvent,
    previous: BreakerSnapshot | None,
    current: BreakerSnapshot,
    config: MitigationBlockConfig,
    config_hash: str,
) -> MitigationEvidence:
    return MitigationEvidence(
        config,
        origin,
        previous,
        current,
        provenance(
            series=current.provenance.series,
            producer="mitigation-evidence",
            configuration_hash=config_hash,
            input_prefix_hash=current.provenance.input_prefix_hash,
            available_at=current.provenance.available_at,
            key={"original_ob": origin.event_id, "upstream": current.provenance.evidence_id},
            source_candles=evidence_sources(previous, current),
            dependencies=evidence_references(origin, previous, current),
        ),
    )


def build_block(evidence: MitigationEvidence) -> MitigationBlock:
    meta = evidence.provenance
    return MitigationBlock(
        evidence,
        provenance(
            series=meta.series,
            producer="mitigation-block",
            configuration_hash=meta.configuration_hash,
            input_prefix_hash=meta.input_prefix_hash,
            available_at=meta.available_at,
            key={"evidence": meta.evidence_id, "original_ob": evidence.original_ob_id},
            source_candles=meta.source_candles,
            dependencies=(meta.as_reference(),),
        ),
    )


def snapshot_provenance(
    previous: BreakerSnapshot | None,
    current: BreakerSnapshot,
    evidence: tuple[MitigationEvidence, ...],
    events: tuple[MitigationBlock, ...],
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
        producer="mitigation-frame",
        configuration_hash=config_hash,
        input_prefix_hash=current.provenance.input_prefix_hash,
        available_at=current.provenance.available_at,
        key={"upstream": current.provenance.evidence_id},
        source_candles=(observation(current).reference,),
        dependencies=refs,
    )
