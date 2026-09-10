"""Immutable shift evidence, event and snapshot; direction labels are not orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.displacement.models import DisplacementEvent
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.models import FVGEvent
from smcsignal.analysis.liquidity.models import (
    LiquidityPool,
    StructureContext,
    SweepEvent,
    SwingEvidence,
    _metadata,
)
from smcsignal.analysis.models import StructureEvent, StructureEventKind, TrendDirection
from smcsignal.analysis.mss.calculation import (
    MSSDirection,
    ShiftMatch,
    qualifying_shift,
    relationships,
    source_frame,
    validate_pair,
)
from smcsignal.analysis.mss.config import METHODOLOGY_VERSION, MSSConfig
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.premium_discount.calculation import PDClassification
from smcsignal.analysis.premium_discount.models import PDArrayContext, PDSnapshot
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    ProvenancedEvidence,
)


def evidence_sources(
    previous: PDSnapshot, current: PDSnapshot, match: ShiftMatch
) -> tuple[CandleReference, ...]:
    refs: dict[int, CandleReference] = {}
    for ref in (
        match.broken_level.pivot.reference,
        match.broken_level.confirmation.reference,
        previous.observation.reference,
        current.observation.reference,
    ):
        if ref.candle_index in refs and refs[ref.candle_index] != ref:
            raise AnalysisInputError("MSS source references conflict at the same observed index")
        refs[ref.candle_index] = ref
    return tuple(refs[i] for i in sorted(refs))


def evidence_references(
    previous: PDSnapshot, current: PDSnapshot, match: ShiftMatch
) -> tuple[EvidenceReference, ...]:
    related = relationships(current, match)
    before, now = source_frame(previous).liquidity.context, source_frame(current).liquidity.context
    refs = [
        previous.provenance.as_reference(),
        current.provenance.as_reference(),
        before.provenance.as_reference(),
        now.provenance.as_reference(),
        match.broken_level.provenance.as_reference(),
        match.displacement.provenance.as_reference(),
    ]
    records: tuple[ProvenancedEvidence, ...] = (
        *related.broken_level_pools,
        *related.swept_pools,
        *match.displacement.preceding_sweeps,
        *related.concurrent_fvgs,
        *related.displacement_order_blocks,
        *related.pd_arrays,
    )
    refs.extend(record.provenance.as_reference() for record in records)
    return tuple(dict.fromkeys(refs))


@dataclass(frozen=True, slots=True)
class MSSEvidence:
    settings: MSSConfig
    previous: PDSnapshot
    current: PDSnapshot
    provenance: EvidenceProvenance
    direction: MSSDirection = field(init=False)
    prior_control: TrendDirection = field(init=False)
    prior_structure_context: StructureContext = field(init=False)
    confirmation_structure_context: StructureContext = field(init=False)
    prior_structure_reference: EvidenceReference = field(init=False)
    structure_reference: EvidenceReference = field(init=False)
    prior_control_events: tuple[StructureEvent, ...] = field(init=False)
    structure_break: StructureEvent = field(init=False)
    broken_level: SwingEvidence = field(init=False)
    broken_level_reference: EvidenceReference = field(init=False)
    level_price: Decimal = field(init=False)
    displacement: DisplacementEvent = field(init=False)
    displacement_reference: EvidenceReference = field(init=False)
    broken_level_pools: tuple[LiquidityPool, ...] = field(init=False)
    swept_pools: tuple[LiquidityPool, ...] = field(init=False)
    related_pool_references: tuple[EvidenceReference, ...] = field(init=False)
    preceding_sweeps: tuple[SweepEvent, ...] = field(init=False)
    sweep_references: tuple[EvidenceReference, ...] = field(init=False)
    concurrent_fvgs: tuple[FVGEvent, ...] = field(init=False)
    fvg_references: tuple[EvidenceReference, ...] = field(init=False)
    displacement_order_blocks: tuple[OrderBlockEvent, ...] = field(init=False)
    order_block_references: tuple[EvidenceReference, ...] = field(init=False)
    pd_reference: EvidenceReference = field(init=False)
    pd_classification: PDClassification = field(init=False)
    pd_array_contexts: tuple[PDArrayContext, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, MSSConfig) or not isinstance(self.previous, PDSnapshot):
            raise AnalysisInputError(
                "MSS evidence requires typed strict settings and previous PD context"
            )
        validate_pair(self.previous, self.current)
        match = qualifying_shift(self.previous, self.current)
        if match is None:
            raise AnalysisInputError(
                "MSS requires known opposing control/level, CHoCH, and matching displacement"
            )
        _metadata(
            self.provenance,
            self.current.observation,
            evidence_references(self.previous, self.current, match),
        )
        if (
            self.provenance.source_candles != evidence_sources(self.previous, self.current, match)
            or self.provenance.input_prefix_hash != self.current.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "MSS evidence must retain exact sources and the current consumed prefix"
            )
        before, now = (
            source_frame(self.previous).liquidity.context,
            source_frame(self.current).liquidity.context,
        )
        related = relationships(self.current, match)
        for name, value in (
            ("direction", match.direction),
            ("prior_control", before.snapshot.trend.direction),
            ("prior_structure_context", before),
            ("confirmation_structure_context", now),
            ("prior_structure_reference", before.provenance.as_reference()),
            ("structure_reference", now.provenance.as_reference()),
            (
                "prior_control_events",
                tuple(
                    e
                    for e in before.snapshot.events
                    if e.kind == StructureEventKind.BOS
                    and e.direction == before.snapshot.trend.direction
                ),
            ),
            ("structure_break", match.structure_break),
            ("broken_level", match.broken_level),
            ("broken_level_reference", match.broken_level.provenance.as_reference()),
            ("level_price", match.structure_break.level.price),
            ("displacement", match.displacement),
            ("displacement_reference", match.displacement.provenance.as_reference()),
            ("broken_level_pools", related.broken_level_pools),
            ("swept_pools", related.swept_pools),
            (
                "related_pool_references",
                tuple(
                    dict.fromkeys(
                        p.provenance.as_reference()
                        for p in (*related.broken_level_pools, *related.swept_pools)
                    )
                ),
            ),
            ("preceding_sweeps", match.displacement.preceding_sweeps),
            ("sweep_references", match.displacement.preceding_sweep_references),
            ("concurrent_fvgs", related.concurrent_fvgs),
            ("fvg_references", tuple(g.provenance.as_reference() for g in related.concurrent_fvgs)),
            ("displacement_order_blocks", related.displacement_order_blocks),
            (
                "order_block_references",
                tuple(b.provenance.as_reference() for b in related.displacement_order_blocks),
            ),
            ("pd_reference", self.current.provenance.as_reference()),
            ("pd_classification", self.current.classification),
            ("pd_array_contexts", related.pd_arrays),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class MSSEvent:
    evidence: MSSEvidence
    provenance: EvidenceProvenance
    event_id: str = field(init=False)
    symbol: str = field(init=False)
    timeframe: str = field(init=False)
    price_unit: str = field(init=False)
    direction: MSSDirection = field(init=False)
    detection_index: int = field(init=False)
    timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)
    methodology_version: str = field(init=False, default=METHODOLOGY_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, MSSEvidence):
            raise AnalysisInputError("MSS event requires immutable qualified MSSEvidence")
        current = self.evidence.current
        _metadata(self.provenance, current.observation, (self.evidence.provenance.as_reference(),))
        if (
            self.provenance.source_candles != self.evidence.provenance.source_candles
            or self.provenance.input_prefix_hash != self.evidence.provenance.input_prefix_hash
            or self.provenance.configuration_hash != self.evidence.provenance.configuration_hash
        ):
            raise AnalysisInputError("MSS event provenance must match its qualified evidence")
        for name, value in (
            ("event_id", self.provenance.evidence_id),
            ("symbol", self.provenance.series.symbol),
            ("timeframe", self.provenance.series.timeframe),
            ("price_unit", source_frame(current).price_unit),
            ("direction", self.evidence.direction),
            ("detection_index", current.observation.reference.candle_index),
            ("timestamp", current.observation.reference.opened_at),
            ("available_at", current.observation.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class MSSSnapshot:
    settings: MSSConfig
    upstream: PDSnapshot
    previous: PDSnapshot | None
    evidence: tuple[MSSEvidence, ...]
    events: tuple[MSSEvent, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, MSSConfig):
            raise AnalysisInputError("MSS snapshot requires strict typed settings")
        validate_pair(self.previous, self.upstream)
        if (
            not isinstance(self.evidence, tuple)
            or len(self.evidence) > 1
            or not all(isinstance(e, MSSEvidence) for e in self.evidence)
        ):
            raise AnalysisInputError("MSS evidence deltas must be a typed immutable zero/one tuple")
        if (
            not isinstance(self.events, tuple)
            or len(self.events) > 1
            or not all(isinstance(e, MSSEvent) for e in self.events)
        ):
            raise AnalysisInputError("MSS event deltas must be a typed immutable zero/one tuple")
        if (
            bool(self.evidence) != (qualifying_shift(self.previous, self.upstream) is not None)
            or tuple(e.evidence for e in self.events) != self.evidence
        ):
            raise AnalysisInputError("MSS outputs must exactly match qualifying existing evidence")
        for item in self.evidence:
            if (
                item.settings != self.settings
                or item.previous != self.previous
                or item.current != self.upstream
            ):
                raise AnalysisInputError(
                    "MSS evidence must belong to this exact publication context"
                )
        refs = [self.upstream.provenance.as_reference()]
        if self.previous is not None:
            refs.append(self.previous.provenance.as_reference())
        records: tuple[ProvenancedEvidence, ...] = (*self.evidence, *self.events)
        refs.extend(e.provenance.as_reference() for e in records)
        _metadata(self.provenance, self.upstream.observation, tuple(refs))
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("MSS snapshot must reuse the current upstream prefix")
