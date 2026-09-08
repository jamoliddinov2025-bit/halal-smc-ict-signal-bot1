"""Versioned FVG identity using existing canonical encoding and provenance."""

from smcsignal.analysis.displacement.calculation import MAX_EXACT_DIGITS
from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.fvg.calculation import gap_geometry
from smcsignal.analysis.fvg.config import METHODOLOGY_VERSION, FVGConfig
from smcsignal.analysis.fvg.models import related_references
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference


def configuration_artifact(config: FVGConfig, price_unit: str) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "price_unit": price_unit,
            "max_exact_digits": MAX_EXACT_DIGITS,
        }
    )


def fvg_provenance(
    window: tuple[DisplacementSnapshot, ...],
    config_hash: str,
    *,
    producer: str,
    extra: tuple[EvidenceReference, ...] = (),
) -> EvidenceProvenance:
    current = window[-1]
    geometry = (
        gap_geometry(window[0].metrics.observation.candle, current.metrics.observation.candle)
        if len(window) == 3
        else None
    )
    return provenance(
        series=current.provenance.series,
        producer=producer,
        configuration_hash=config_hash,
        input_prefix_hash=current.provenance.input_prefix_hash,
        available_at=current.provenance.available_at,
        key={"window": tuple(f.provenance.evidence_id for f in window), "geometry": geometry},
        source_candles=tuple(f.metrics.observation.reference for f in window),
        dependencies=related_references(window) + extra,
    )
