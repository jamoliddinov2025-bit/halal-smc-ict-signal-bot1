"""Phase 5 artifacts using the existing canonical codec and provenance factory."""

from __future__ import annotations

from smcsignal.analysis.displacement.calculation import MAX_EXACT_DIGITS, RATIO_PRECISION
from smcsignal.analysis.displacement.config import (
    ATR_METHODOLOGY_VERSION,
    METHODOLOGY_VERSION,
    DisplacementConfig,
)
from smcsignal.analysis.displacement.models import (
    ATRReference,
    DisplacementEvent,
    DisplacementMetrics,
)
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.liquidity.models import ObservedCandle, StructureContext, SweepEvent
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference


def configuration_artifacts(config: DisplacementConfig, price_unit: str) -> tuple[bytes, bytes]:
    common = {
        "price_unit": price_unit,
        "ratio_precision": RATIO_PRECISION,
        "max_exact_digits": MAX_EXACT_DIGITS,
    }
    return (
        canonical_bytes({**common, "methodology": METHODOLOGY_VERSION, "settings": config}),
        canonical_bytes(
            {**common, "methodology": ATR_METHODOLOGY_VERSION, "period": config.atr_period}
        ),
    )


def build_atr(
    observations: tuple[ObservedCandle, ...],
    context: StructureContext,
    period: int,
    config_hash: str,
) -> ATRReference:
    return ATRReference(
        period,
        observations,
        context,
        provenance(
            series=context.provenance.series,
            producer="displacement-atr",
            configuration_hash=config_hash,
            input_prefix_hash=context.provenance.input_prefix_hash,
            available_at=context.provenance.available_at,
            key={"period": period, "context": context.provenance.evidence_id},
            source_candles=tuple(c.reference for c in observations),
            dependencies=(context.provenance.as_reference(),),
        ),
    )


def displacement_provenance(
    *,
    producer: str,
    context: StructureContext,
    config_hash: str,
    metrics: DisplacementMetrics,
    sweeps: tuple[SweepEvent, ...],
    extra: tuple[EvidenceReference, ...] = (),
) -> EvidenceProvenance:
    reference = metrics.atr_reference
    dependencies = [context.provenance.as_reference()]
    if reference is not None:
        dependencies.append(reference.provenance.as_reference())
    dependencies.extend(s.provenance.as_reference() for s in sweeps)
    dependencies.extend(extra)
    return provenance(
        series=context.provenance.series,
        producer=producer,
        configuration_hash=config_hash,
        input_prefix_hash=context.provenance.input_prefix_hash,
        available_at=context.provenance.available_at,
        key={
            "context": context.provenance.evidence_id,
            "body_size": metrics.body_size,
            "range_size": metrics.range_size,
            "direction": metrics.direction,
            "close_location_ratio": metrics.close_location_ratio,
            "body_atr_ratio": metrics.body_atr_ratio,
            "range_atr_ratio": metrics.range_atr_ratio,
        },
        source_candles=(metrics.observation.reference,),
        dependencies=tuple(dependencies),
    )


def build_event(
    metrics: DisplacementMetrics,
    context: StructureContext,
    sweeps: tuple[SweepEvent, ...],
    config: DisplacementConfig,
    price_unit: str,
    config_hash: str,
) -> DisplacementEvent:
    return DisplacementEvent(
        config,
        price_unit,
        metrics,
        context,
        sweeps,
        displacement_provenance(
            producer="displacement-event",
            context=context,
            config_hash=config_hash,
            metrics=metrics,
            sweeps=sweeps,
        ),
    )
