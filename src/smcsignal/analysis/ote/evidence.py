"""OTE identities using the existing canonical/provenance contract."""

from smcsignal.analysis.displacement.calculation import MAX_EXACT_DIGITS
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.liquidity.models import ObservedCandle
from smcsignal.analysis.ote.config import METHODOLOGY_VERSION, OTEConfig
from smcsignal.analysis.ote.models import OTEObservation, OTEZone
from smcsignal.analysis.premium_discount.models import DealingRange, PDSnapshot
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference


def configuration_artifact(config: OTEConfig, price_unit: str) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "price_unit": price_unit,
            "max_exact_digits": MAX_EXACT_DIGITS,
            "selection": "current_phase8_dealing_range",
            "observation_timing": "range_known_before_bar_open",
        }
    )


def build_zone(dealing_range: DealingRange, config: OTEConfig, config_hash: str) -> OTEZone:
    meta = dealing_range.provenance
    return OTEZone(
        dealing_range,
        config,
        provenance(
            series=meta.series,
            producer="ote-zone",
            configuration_hash=config_hash,
            input_prefix_hash=meta.input_prefix_hash,
            available_at=meta.available_at,
            key={"range": meta.evidence_id, "settings": config},
            source_candles=meta.source_candles,
            dependencies=(meta.as_reference(),),
        ),
    )


def build_observation(
    config: OTEConfig,
    zone: OTEZone | None,
    evaluation: ObservedCandle,
    config_hash: str,
    input_prefix_hash: str,
) -> OTEObservation:
    dependencies = (zone.provenance.as_reference(),) if zone is not None else ()
    return OTEObservation(
        config,
        zone,
        evaluation,
        provenance(
            series=evaluation.reference.series,
            producer="ote-observation",
            configuration_hash=config_hash,
            input_prefix_hash=input_prefix_hash,
            available_at=evaluation.available_at,
            key={
                "zone": None if zone is None else zone.provenance.evidence_id,
                "close": evaluation.candle.close,
            },
            source_candles=(evaluation.reference,),
            dependencies=dependencies,
        ),
    )


def snapshot_provenance(
    upstream: PDSnapshot,
    zone: OTEZone | None,
    observation: OTEObservation,
    config_hash: str,
) -> EvidenceProvenance:
    refs: tuple[EvidenceReference, ...] = (
        upstream.provenance.as_reference(),
        observation.provenance.as_reference(),
    )
    if zone is not None:
        refs += (zone.provenance.as_reference(),)
    return provenance(
        series=upstream.provenance.series,
        producer="ote-frame",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=upstream.provenance.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "observation": observation.observation_id,
        },
        source_candles=(upstream.observation.reference,),
        dependencies=refs,
    )
