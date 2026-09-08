"""MSS configuration and identities through the unchanged provenance factory."""

from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.mss.calculation import qualifying_shift
from smcsignal.analysis.mss.config import METHODOLOGY_VERSION, MSSConfig
from smcsignal.analysis.mss.models import (
    MSSEvent,
    MSSEvidence,
    evidence_references,
    evidence_sources,
)
from smcsignal.analysis.premium_discount.models import PDSnapshot
from smcsignal.analysis.provenance import EvidenceProvenance, ProvenancedEvidence


def configuration_artifact(config: MSSConfig, price_unit: str) -> bytes:
    return canonical_bytes(
        {"methodology": METHODOLOGY_VERSION, "settings": config, "price_unit": price_unit}
    )


def build_evidence(
    previous: PDSnapshot, current: PDSnapshot, config: MSSConfig, config_hash: str
) -> MSSEvidence:
    match = qualifying_shift(previous, current)
    assert match is not None
    return MSSEvidence(
        config,
        previous,
        current,
        provenance(
            series=current.provenance.series,
            producer="mss-evidence",
            configuration_hash=config_hash,
            input_prefix_hash=current.provenance.input_prefix_hash,
            available_at=current.provenance.available_at,
            key={
                "direction": match.direction,
                "structure_break": match.structure_break,
                "level": match.broken_level.provenance.evidence_id,
                "displacement": match.displacement.event_id,
            },
            source_candles=evidence_sources(previous, current, match),
            dependencies=evidence_references(previous, current, match),
        ),
    )


def build_event(evidence: MSSEvidence) -> MSSEvent:
    meta = evidence.provenance
    return MSSEvent(
        evidence,
        provenance(
            series=meta.series,
            producer="mss-event",
            configuration_hash=meta.configuration_hash,
            input_prefix_hash=meta.input_prefix_hash,
            available_at=meta.available_at,
            key={"evidence": meta.evidence_id, "direction": evidence.direction},
            source_candles=meta.source_candles,
            dependencies=(meta.as_reference(),),
        ),
    )


def snapshot_provenance(
    previous: PDSnapshot | None,
    current: PDSnapshot,
    evidence: tuple[MSSEvidence, ...],
    events: tuple[MSSEvent, ...],
    config_hash: str,
) -> EvidenceProvenance:
    refs = (current.provenance.as_reference(),) + (
        (previous.provenance.as_reference(),) if previous is not None else ()
    )
    records: tuple[ProvenancedEvidence, ...] = (*evidence, *events)
    refs += tuple(e.provenance.as_reference() for e in records)
    return provenance(
        series=current.provenance.series,
        producer="mss-frame",
        configuration_hash=config_hash,
        input_prefix_hash=current.provenance.input_prefix_hash,
        available_at=current.provenance.available_at,
        key={"upstream": current.provenance.evidence_id},
        source_candles=(current.observation.reference,),
        dependencies=refs,
    )
