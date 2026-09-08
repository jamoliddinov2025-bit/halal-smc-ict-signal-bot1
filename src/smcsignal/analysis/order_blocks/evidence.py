"""OB artifacts through the existing exact canonical/provenance machinery."""

from smcsignal.analysis.displacement.calculation import MAX_EXACT_DIGITS
from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.fvg.models import FVGSnapshot
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.order_blocks.calculation import (
    formation_candidate,
    matching_fvg,
    matching_structure,
)
from smcsignal.analysis.order_blocks.config import METHODOLOGY_VERSION, OrderBlockConfig
from smcsignal.analysis.order_blocks.models import (
    OrderBlockEvent,
    formation_references,
    formation_sources,
)
from smcsignal.analysis.provenance import EvidenceProvenance


def configuration_artifact(config: OrderBlockConfig, price_unit: str) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "price_unit": price_unit,
            "max_exact_digits": MAX_EXACT_DIGITS,
        }
    )


def build_event(
    history: tuple[DisplacementSnapshot, ...],
    displacement_frame: DisplacementSnapshot,
    publication: FVGSnapshot,
    config: OrderBlockConfig,
    config_hash: str,
) -> OrderBlockEvent:
    candidate = formation_candidate(history, displacement_frame, config)
    assert candidate is not None
    displacement = displacement_frame.events[0]
    gap = matching_fvg(displacement_frame, publication) if config.require_fvg else None
    metadata = provenance(
        series=publication.provenance.series,
        producer="order-block-event",
        configuration_hash=config_hash,
        input_prefix_hash=publication.provenance.input_prefix_hash,
        available_at=publication.provenance.available_at,
        key={
            "candidate": candidate.frame.provenance.evidence_id,
            "displacement": displacement.event_id,
            "zone": (candidate.lower, candidate.upper, candidate.size),
            "classification": candidate.classification,
            "structure": matching_structure(displacement_frame),
        },
        source_candles=formation_sources(history, displacement_frame, publication),
        dependencies=formation_references(history, displacement_frame, publication, gap),
    )
    return OrderBlockEvent(config, history, displacement_frame, publication, metadata)


def snapshot_provenance(
    publication: FVGSnapshot, events: tuple[OrderBlockEvent, ...], config_hash: str
) -> EvidenceProvenance:
    return provenance(
        series=publication.provenance.series,
        producer="order-block-frame",
        configuration_hash=config_hash,
        input_prefix_hash=publication.provenance.input_prefix_hash,
        available_at=publication.provenance.available_at,
        key={"upstream": publication.provenance.evidence_id},
        source_candles=(publication.upstream.metrics.observation.reference,),
        dependencies=(
            publication.provenance.as_reference(),
            *(e.provenance.as_reference() for e in events),
        ),
    )
