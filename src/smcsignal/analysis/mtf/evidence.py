"""MTF identities using the existing canonical/provenance contract."""

from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.mtf.calculation import (
    KIND_RANK,
    MTFEvidenceKind,
    htf_direction,
    publication_records,
    state_records,
)
from smcsignal.analysis.mtf.config import METHODOLOGY_VERSION, MTFConfig
from smcsignal.analysis.mtf.models import MTFEvidenceReference, MTFRelation
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference


def configuration_artifact(config: MTFConfig) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "selection": "latest_completed_htf_ote_snapshot",
            "availability": "htf_available_at_lte_primary_open",
            "direction": "phase3_trend_on_latest_eligible_htf",
            "confluence": "unweighted_label_agreement",
        }
    )


def make_reference(
    timeframe: str, kind: MTFEvidenceKind, provenance_record: EvidenceProvenance
) -> MTFEvidenceReference:
    return MTFEvidenceReference(timeframe, kind, provenance_record.as_reference())


def freeze_evidence(
    published: tuple[MTFEvidenceReference, ...],
    latest: OTESnapshot | None,
    timeframe: str,
) -> tuple[MTFEvidenceReference, ...]:
    items = list(published)
    if latest is not None:
        items.extend(
            make_reference(timeframe, kind, record) for kind, record in state_records(latest)
        )
    unique: dict[str, MTFEvidenceReference] = {}
    for item in items:
        unique.setdefault(item.source.evidence_id, item)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.source.available_at,
                KIND_RANK[item.kind],
                item.source.evidence_id,
            ),
        )
    )


def publication_refs(frame: OTESnapshot, timeframe: str) -> tuple[MTFEvidenceReference, ...]:
    records = publication_records(frame)
    return tuple(make_reference(timeframe, kind, record) for kind, record in records)


def build_relation(
    timeframe: str,
    latest: OTESnapshot | None,
    evidence: tuple[MTFEvidenceReference, ...],
) -> MTFRelation:
    direction, reason = htf_direction(latest)
    return MTFRelation(
        timeframe,
        latest,
        direction,
        None if latest is None else latest.upstream.classification,
        None if latest is None else latest.classification,
        evidence,
        reason,
    )


def snapshot_provenance(
    upstream: OTESnapshot,
    relations: tuple[MTFRelation, ...],
    config_hash: str,
) -> EvidenceProvenance:
    refs: tuple[EvidenceReference, ...] = (upstream.provenance.as_reference(),) + tuple(
        relation.latest.provenance.as_reference()
        for relation in relations
        if relation.latest is not None
    )
    return provenance(
        series=upstream.provenance.series,
        producer="mtf-frame",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=upstream.provenance.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "higher": tuple(
                None if relation.latest is None else relation.latest.provenance.evidence_id
                for relation in relations
            ),
        },
        source_candles=(upstream.upstream.observation.reference,),
        dependencies=refs,
    )
