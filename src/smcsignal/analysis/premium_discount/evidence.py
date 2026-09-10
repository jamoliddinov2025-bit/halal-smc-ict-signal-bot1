"""Sidecar and range artifacts using the existing canonical/provenance factories."""

from smcsignal.analysis.displacement.calculation import MAX_EXACT_DIGITS
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.liquidity.models import ObservedCandle, StructureContext, SwingEvidence
from smcsignal.analysis.order_blocks.models import OrderBlockSnapshot
from smcsignal.analysis.premium_discount.calculation import PDSubject, array_prices
from smcsignal.analysis.premium_discount.config import METHODOLOGY_VERSION, RANGE_VERSION, PDConfig
from smcsignal.analysis.premium_discount.models import (
    DealingRange,
    Equilibrium,
    PDArrayContext,
    range_sources,
)
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference


def configuration_artifacts(config: PDConfig, price_unit: str) -> tuple[bytes, bytes]:
    common = {"price_unit": price_unit, "max_exact_digits": MAX_EXACT_DIGITS}
    return (
        canonical_bytes({**common, "methodology": METHODOLOGY_VERSION, "settings": config}),
        canonical_bytes({**common, "methodology": RANGE_VERSION}),
    )


def build_range(
    low: SwingEvidence,
    high: SwingEvidence,
    context: StructureContext,
    price_unit: str,
    config_hash: str,
) -> DealingRange:
    return DealingRange(
        low,
        high,
        context,
        price_unit,
        provenance(
            series=context.provenance.series,
            producer="dealing-range",
            configuration_hash=config_hash,
            input_prefix_hash=context.provenance.input_prefix_hash,
            available_at=context.provenance.available_at,
            key={
                "low": low.provenance.evidence_id,
                "high": high.provenance.evidence_id,
                "context": context.provenance.evidence_id,
            },
            source_candles=range_sources(low, high, context),
            dependencies=(
                low.provenance.as_reference(),
                high.provenance.as_reference(),
                context.provenance.as_reference(),
            ),
        ),
    )


def build_equilibrium(
    dealing_range: DealingRange, config: PDConfig, config_hash: str
) -> Equilibrium:
    meta = dealing_range.provenance
    return Equilibrium(
        dealing_range,
        config,
        provenance(
            series=meta.series,
            producer="pd-equilibrium",
            configuration_hash=config_hash,
            input_prefix_hash=meta.input_prefix_hash,
            available_at=meta.available_at,
            key={"range": meta.evidence_id},
            source_candles=meta.source_candles,
            dependencies=(meta.as_reference(),),
        ),
    )


def build_array_context(
    subject: PDSubject,
    evaluation: ObservedCandle,
    dealing_range: DealingRange | None,
    equilibrium: Equilibrium | None,
    config_hash: str,
) -> PDArrayContext:
    dependencies: tuple[EvidenceReference, ...] = (subject.provenance.as_reference(),)
    if dealing_range is not None and equilibrium is not None:
        dependencies += (
            dealing_range.provenance.as_reference(),
            equilibrium.provenance.as_reference(),
        )
    return PDArrayContext(
        subject,
        evaluation,
        dealing_range,
        equilibrium,
        provenance(
            series=evaluation.reference.series,
            producer="pd-array-context",
            configuration_hash=config_hash,
            input_prefix_hash=subject.provenance.input_prefix_hash,
            available_at=evaluation.available_at,
            key={"subject": subject.provenance.evidence_id, "prices": array_prices(subject)},
            source_candles=(evaluation.reference,),
            dependencies=dependencies,
        ),
    )


def snapshot_provenance(
    upstream: OrderBlockSnapshot,
    low: SwingEvidence | None,
    high: SwingEvidence | None,
    dealing_range: DealingRange | None,
    equilibrium: Equilibrium | None,
    arrays: tuple[PDArrayContext, ...],
    config_hash: str,
) -> EvidenceProvenance:
    observation = upstream.upstream.upstream.metrics.observation
    dependencies = [upstream.provenance.as_reference()]
    for record in (low, high, dealing_range, equilibrium, *arrays):
        if record is not None:
            dependencies.append(record.provenance.as_reference())
    return provenance(
        series=upstream.provenance.series,
        producer="pd-frame",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=upstream.provenance.available_at,
        key={"upstream": upstream.provenance.evidence_id, "close": observation.candle.close},
        source_candles=(observation.reference,),
        dependencies=tuple(dependencies),
    )
