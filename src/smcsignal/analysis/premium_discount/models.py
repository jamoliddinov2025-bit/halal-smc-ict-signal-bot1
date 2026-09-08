"""Immutable ranges, exact equilibrium, classifications, and non-mutating sidecars."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.displacement.calculation import difference
from smcsignal.analysis.displacement.models import DisplacementEvent
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.models import FVGEvent
from smcsignal.analysis.liquidity.models import (
    LiquidityPool,
    ObservedCandle,
    StructureContext,
    SweepEvent,
    SwingEvidence,
    _metadata,
)
from smcsignal.analysis.models import SwingKind, TrendDirection
from smcsignal.analysis.order_blocks.models import OrderBlockEvent, OrderBlockSnapshot
from smcsignal.analysis.premium_discount.calculation import (
    PDArrayKind,
    PDClassification,
    PDSubject,
    RangeStatus,
    array_prices,
    classify_price,
    equilibrium_band,
    pair_status,
    published_arrays,
)
from smcsignal.analysis.premium_discount.config import PDConfig
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    SeriesProvenance,
    _text,
)


def range_sources(
    low: SwingEvidence, high: SwingEvidence, context: StructureContext
) -> tuple[CandleReference, ...]:
    refs: dict[int, CandleReference] = {}
    for ref in (
        low.pivot.reference,
        low.confirmation.reference,
        high.pivot.reference,
        high.confirmation.reference,
        context.observation.reference,
    ):
        if ref.candle_index in refs and refs[ref.candle_index] != ref:
            raise AnalysisInputError("range source references disagree at the same index")
        refs[ref.candle_index] = ref
    return tuple(refs[i] for i in sorted(refs))


@dataclass(frozen=True, slots=True)
class DealingRange:
    low_swing: SwingEvidence
    high_swing: SwingEvidence
    context: StructureContext
    price_unit: str
    provenance: EvidenceProvenance
    range_id: str = field(init=False)
    direction: TrendDirection = field(init=False)
    lower_boundary: Decimal = field(init=False)
    upper_boundary: Decimal = field(init=False)
    size: Decimal = field(init=False)
    start_index: int = field(init=False)
    end_index: int = field(init=False)
    confirmation_index: int = field(init=False)
    confirmation_timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        _text(self.price_unit, "price_unit")
        if (
            not isinstance(self.low_swing, SwingEvidence)
            or not isinstance(self.high_swing, SwingEvidence)
            or not isinstance(self.context, StructureContext)
        ):
            raise AnalysisInputError(
                "dealing range requires existing confirmed swing evidence and context"
            )
        low, high = self.low_swing, self.high_swing
        if low.swing.kind != SwingKind.LOW or high.swing.kind != SwingKind.HIGH:
            raise AnalysisInputError("dealing range requires one low and one high")
        if pair_status(low, high) != RangeStatus.CONFIRMED:
            raise AnalysisInputError(
                "dealing range requires distinct ordered pivots and a positive span"
            )
        current = self.context.observation
        if (
            max(low.swing.confirmed_index, high.swing.confirmed_index)
            != current.reference.candle_index
        ):
            raise AnalysisInputError(
                "new dealing range must publish when its latest endpoint confirms"
            )
        for endpoint in (low, high):
            if (
                endpoint.provenance.series != current.reference.series
                or endpoint.provenance.available_at > current.available_at
            ):
                raise AnalysisInputError("range endpoints must be known in the same series")
        _metadata(
            self.provenance,
            current,
            (
                low.provenance.as_reference(),
                high.provenance.as_reference(),
                self.context.provenance.as_reference(),
            ),
        )
        if (
            self.provenance.source_candles != range_sources(low, high, self.context)
            or self.provenance.input_prefix_hash != self.context.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "range must retain its exact endpoint sources and confirmation prefix"
            )
        first, last = sorted((low.swing.pivot_index, high.swing.pivot_index))
        for name, value in (
            ("range_id", self.provenance.evidence_id),
            (
                "direction",
                TrendDirection.BULLISH
                if low.swing.pivot_index < high.swing.pivot_index
                else TrendDirection.BEARISH,
            ),
            ("lower_boundary", low.swing.price),
            ("upper_boundary", high.swing.price),
            ("size", difference(high.swing.price, low.swing.price)),
            ("start_index", first),
            ("end_index", last),
            ("confirmation_index", current.reference.candle_index),
            ("confirmation_timestamp", current.reference.opened_at),
            ("available_at", current.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class Equilibrium:
    dealing_range: DealingRange
    settings: PDConfig
    provenance: EvidenceProvenance
    midpoint: Decimal = field(init=False)
    half_width: Decimal = field(init=False)
    lower_boundary: Decimal = field(init=False)
    upper_boundary: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.dealing_range, DealingRange) or not isinstance(
            self.settings, PDConfig
        ):
            raise AnalysisInputError(
                "equilibrium requires a confirmed dealing range and PD settings"
            )
        source = self.dealing_range
        _metadata(self.provenance, source.context.observation, (source.provenance.as_reference(),))
        if (
            self.provenance.source_candles != source.provenance.source_candles
            or self.provenance.input_prefix_hash != source.provenance.input_prefix_hash
        ):
            raise AnalysisInputError("equilibrium must retain its range sources and prefix")
        values = equilibrium_band(
            source.lower_boundary,
            source.upper_boundary,
            self.settings.equilibrium_half_width_fraction,
        )
        for name, value in zip(
            ("midpoint", "half_width", "lower_boundary", "upper_boundary"), values, strict=True
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class PDContextReference:
    """Timeframe-bearing references usable by future context consumers, not an HTF join."""

    range_reference: EvidenceReference
    equilibrium_reference: EvidenceReference

    def __post_init__(self) -> None:
        if not isinstance(self.range_reference, EvidenceReference) or not isinstance(
            self.equilibrium_reference, EvidenceReference
        ):
            raise AnalysisInputError("PD context requires exact immutable evidence references")
        if (
            self.range_reference.series != self.equilibrium_reference.series
            or self.equilibrium_reference.available_at < self.range_reference.available_at
        ):
            raise AnalysisInputError(
                "PD context references must share a series and causal availability"
            )
        if self.range_reference.evidence_id == self.equilibrium_reference.evidence_id:
            raise AnalysisInputError("range and equilibrium must be distinct evidence snapshots")

    @property
    def series(self) -> SeriesProvenance:
        return self.range_reference.series

    @property
    def available_at(self) -> datetime:
        return self.equilibrium_reference.available_at


def context_reference(
    dealing_range: DealingRange | None, equilibrium: Equilibrium | None, evaluation: ObservedCandle
) -> PDContextReference | None:
    if dealing_range is None and equilibrium is None:
        return None
    if (
        not isinstance(dealing_range, DealingRange)
        or not isinstance(equilibrium, Equilibrium)
        or equilibrium.dealing_range != dealing_range
    ):
        raise AnalysisInputError("PD context requires a paired range and its equilibrium")
    if (
        dealing_range.provenance.series != evaluation.reference.series
        or equilibrium.provenance.series != evaluation.reference.series
    ):
        raise AnalysisInputError(
            "Phase 8 evaluation is local-series only; no HTF joining is implemented"
        )
    if (
        dealing_range.confirmation_index > evaluation.reference.candle_index
        or max(dealing_range.available_at, equilibrium.provenance.available_at)
        > evaluation.available_at
    ):
        raise AnalysisInputError("PD cannot use range or equilibrium evidence from the future")
    return PDContextReference(
        dealing_range.provenance.as_reference(), equilibrium.provenance.as_reference()
    )


def boundaries(
    dealing_range: DealingRange | None, equilibrium: Equilibrium | None
) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    if dealing_range is None or equilibrium is None:
        return None
    return (
        dealing_range.lower_boundary,
        dealing_range.upper_boundary,
        equilibrium.lower_boundary,
        equilibrium.upper_boundary,
    )


@dataclass(frozen=True, slots=True)
class PDArrayContext:
    """Publication-time annotation; the original immutable subject and ID are preserved."""

    subject: PDSubject
    evaluation: ObservedCandle
    dealing_range: DealingRange | None
    equilibrium: Equilibrium | None
    provenance: EvidenceProvenance
    source_reference: EvidenceReference = field(init=False)
    context: PDContextReference | None = field(init=False)
    kind: PDArrayKind = field(init=False)
    price_unit: str = field(init=False)
    lower_price: Decimal = field(init=False)
    upper_price: Decimal = field(init=False)
    evaluated_price: Decimal = field(init=False)
    classification: PDClassification = field(init=False)
    lower_classification: PDClassification = field(init=False)
    upper_classification: PDClassification = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(
            self.subject, (LiquidityPool, SweepEvent, DisplacementEvent, FVGEvent, OrderBlockEvent)
        ) or not isinstance(self.evaluation, ObservedCandle):
            raise AnalysisInputError(
                "PD arrays require a supported existing subject and evaluation candle"
            )
        facts = array_prices(self.subject)
        if (
            self.subject.provenance.series != self.evaluation.reference.series
            or self.subject.provenance.available_at != self.evaluation.available_at
            or facts.publication_index != self.evaluation.reference.candle_index
        ):
            raise AnalysisInputError(
                "PD sidecars annotate the subject's actual publication candle, not its old origin"
            )
        context = context_reference(self.dealing_range, self.equilibrium, self.evaluation)
        if self.dealing_range is not None and facts.price_unit != self.dealing_range.price_unit:
            raise AnalysisInputError("PD subject and range price units must agree")
        required = (self.subject.provenance.as_reference(),) + (
            (context.range_reference, context.equilibrium_reference) if context is not None else ()
        )
        _metadata(self.provenance, self.evaluation, required)
        if self.provenance.input_prefix_hash != self.subject.provenance.input_prefix_hash:
            raise AnalysisInputError("PD sidecar must use its publication-time source prefix")
        limits = boundaries(self.dealing_range, self.equilibrium)
        for name, value in (
            ("source_reference", self.subject.provenance.as_reference()),
            ("context", context),
            ("kind", facts.kind),
            ("price_unit", facts.price_unit),
            ("lower_price", facts.lower),
            ("upper_price", facts.upper),
            ("evaluated_price", facts.price),
            ("classification", classify_price(facts.price, limits)),
            ("lower_classification", classify_price(facts.lower, limits)),
            ("upper_classification", classify_price(facts.upper, limits)),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class PDSnapshot:
    settings: PDConfig
    upstream: OrderBlockSnapshot
    latest_low: SwingEvidence | None
    latest_high: SwingEvidence | None
    dealing_range: DealingRange | None
    equilibrium: Equilibrium | None
    arrays: tuple[PDArrayContext, ...]
    provenance: EvidenceProvenance
    observation: ObservedCandle = field(init=False)
    evaluated_price: Decimal = field(init=False)
    classification: PDClassification = field(init=False)
    range_status: RangeStatus = field(init=False)
    context: PDContextReference | None = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, PDConfig) or not isinstance(
            self.upstream, OrderBlockSnapshot
        ):
            raise AnalysisInputError(
                "PD snapshot requires typed settings and an existing Order Block frame"
            )
        source = self.upstream.upstream.upstream
        observation = source.metrics.observation
        trend = source.liquidity.context.snapshot.trend
        dependencies = [self.upstream.provenance.as_reference()]
        for endpoint, expected, kind in (
            (self.latest_low, trend.swing_lows[-1] if trend.swing_lows else None, SwingKind.LOW),
            (
                self.latest_high,
                trend.swing_highs[-1] if trend.swing_highs else None,
                SwingKind.HIGH,
            ),
        ):
            if endpoint is None:
                if expected is not None:
                    raise AnalysisInputError("PD omitted the latest already-confirmed swing")
            else:
                if (
                    not isinstance(endpoint, SwingEvidence)
                    or endpoint.swing != expected
                    or endpoint.swing.kind != kind
                    or endpoint.provenance.series != observation.reference.series
                    or endpoint.provenance.available_at > observation.available_at
                ):
                    raise AnalysisInputError(
                        "PD endpoints must be the latest known exact upstream swing evidence"
                    )
                dependencies.append(endpoint.provenance.as_reference())
        status = pair_status(self.latest_low, self.latest_high)
        if (self.dealing_range is not None) != (status == RangeStatus.CONFIRMED):
            raise AnalysisInputError(
                "PD range availability must match the latest opposing-pair status"
            )
        context = context_reference(self.dealing_range, self.equilibrium, observation)
        if context is not None:
            assert self.dealing_range is not None and self.equilibrium is not None
            if (
                self.dealing_range.low_swing != self.latest_low
                or self.dealing_range.high_swing != self.latest_high
                or self.equilibrium.settings != self.settings
                or self.dealing_range.price_unit != source.price_unit
            ):
                raise AnalysisInputError(
                    "PD must use the selected latest pair, settings, and declared price units"
                )
            dependencies.extend((context.range_reference, context.equilibrium_reference))
        if not isinstance(self.arrays, tuple) or not all(
            isinstance(a, PDArrayContext) for a in self.arrays
        ):
            raise AnalysisInputError("PD sidecars must be immutable typed tuples")
        if tuple(a.subject for a in self.arrays) != published_arrays(self.upstream):
            raise AnalysisInputError("PD must annotate exactly the newly published upstream arrays")
        for array in self.arrays:
            if (
                array.evaluation != observation
                or array.dealing_range != self.dealing_range
                or array.equilibrium != self.equilibrium
                or array.price_unit != source.price_unit
            ):
                raise AnalysisInputError("PD arrays must share this exact evaluation context")
            dependencies.append(array.provenance.as_reference())
        _metadata(self.provenance, observation, tuple(dependencies))
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("PD must reuse the current upstream input-prefix hash")
        for name, value in (
            ("observation", observation),
            ("evaluated_price", observation.candle.close),
            (
                "classification",
                classify_price(
                    observation.candle.close, boundaries(self.dealing_range, self.equilibrium)
                ),
            ),
            ("range_status", status),
            ("context", context),
        ):
            object.__setattr__(self, name, value)
