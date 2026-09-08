"""Immutable first-violation assessments and confirmed opposite-zone formations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.breaker_blocks.calculation import (
    BreakerDirection,
    BreakerRejection,
    direction_for,
    rejection_reason,
    validate_frames,
    violates,
)
from smcsignal.analysis.breaker_blocks.config import METHODOLOGY_VERSION, BreakerBlockConfig
from smcsignal.analysis.displacement.models import DisplacementEvent
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.models import FVGEvent
from smcsignal.analysis.liquidity.models import (
    ObservedCandle,
    StructureContext,
    SweepEvent,
    _metadata,
)
from smcsignal.analysis.models import StructureEvent
from smcsignal.analysis.mss.calculation import source_frame
from smcsignal.analysis.mss.models import MSSEvent, MSSSnapshot
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.premium_discount.calculation import PDClassification
from smcsignal.analysis.provenance import CandleReference, EvidenceProvenance, EvidenceReference


def evidence_sources(
    previous: MSSSnapshot | None, current: MSSSnapshot
) -> tuple[CandleReference, ...]:
    return ((previous.upstream.observation.reference,) if previous is not None else ()) + (
        current.upstream.observation.reference,
    )


def evidence_references(
    origin: OrderBlockEvent, previous: MSSSnapshot | None, current: MSSSnapshot
) -> tuple[EvidenceReference, ...]:
    source = source_frame(current.upstream)
    refs = [
        origin.provenance.as_reference(),
        current.provenance.as_reference(),
        current.upstream.provenance.as_reference(),
        source.liquidity.context.provenance.as_reference(),
    ]
    if previous is not None:
        refs.append(previous.provenance.as_reference())
    if source.events:
        displacement = source.events[0]
        refs.append(displacement.provenance.as_reference())
        refs.extend(s.provenance.as_reference() for s in displacement.preceding_sweeps)
    if current.events:
        refs.append(current.events[0].provenance.as_reference())
        refs.extend(g.provenance.as_reference() for g in current.events[0].evidence.concurrent_fvgs)
    return tuple(dict.fromkeys(refs))


@dataclass(frozen=True, slots=True)
class BreakerEvidence:
    """A first closing violation, including why it did or did not form a breaker."""

    settings: BreakerBlockConfig
    original_ob: OrderBlockEvent
    previous: MSSSnapshot | None
    current: MSSSnapshot
    provenance: EvidenceProvenance
    direction: BreakerDirection = field(init=False)
    original_ob_id: str = field(init=False)
    original_ob_reference: EvidenceReference = field(init=False)
    invalidating_candle: ObservedCandle = field(init=False)
    previous_candle: ObservedCandle | None = field(init=False)
    rejection_reason: BreakerRejection | None = field(init=False)
    qualified: bool = field(init=False)
    displacement: DisplacementEvent | None = field(init=False)
    displacement_reference: EvidenceReference | None = field(init=False)
    mss: MSSEvent | None = field(init=False)
    mss_reference: EvidenceReference | None = field(init=False)
    structure_context: StructureContext = field(init=False)
    structure_events: tuple[StructureEvent, ...] = field(init=False)
    structure_reference: EvidenceReference = field(init=False)
    preceding_sweeps: tuple[SweepEvent, ...] = field(init=False)
    sweep_references: tuple[EvidenceReference, ...] = field(init=False)
    concurrent_fvgs: tuple[FVGEvent, ...] = field(init=False)
    fvg_references: tuple[EvidenceReference, ...] = field(init=False)
    pd_reference: EvidenceReference = field(init=False)
    pd_classification: PDClassification = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, BreakerBlockConfig) or not isinstance(
            self.original_ob, OrderBlockEvent
        ):
            raise AnalysisInputError(
                "Breaker evidence requires typed settings and an original Order Block"
            )
        validate_frames(self.previous, self.current)
        observation = self.current.upstream.observation
        source = source_frame(self.current.upstream)
        origin = self.original_ob
        if (
            origin.provenance.series != observation.reference.series
            or origin.price_unit != source.price_unit
            or origin.confirmation_index > observation.reference.candle_index
            or origin.available_at > observation.available_at
        ):
            raise AnalysisInputError(
                "source OB must already exist in this series by the assessment cutoff"
            )
        if not violates(origin, observation.candle.close):
            raise AnalysisInputError(
                "invalidation evidence requires a strict close beyond the opposing OB boundary"
            )
        reason = rejection_reason(origin, self.previous, self.current)
        _metadata(
            self.provenance, observation, evidence_references(origin, self.previous, self.current)
        )
        if (
            self.provenance.source_candles != evidence_sources(self.previous, self.current)
            or self.provenance.input_prefix_hash != self.current.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "Breaker evidence must retain actual close observations and publication prefix"
            )
        displacement = source.events[0] if source.events else None
        mss = self.current.events[0] if self.current.events else None
        sweeps = displacement.preceding_sweeps if displacement is not None else ()
        gaps = mss.evidence.concurrent_fvgs if mss is not None else ()
        context = source.liquidity.context
        for name, value in (
            ("direction", direction_for(origin)),
            ("original_ob_id", origin.event_id),
            ("original_ob_reference", origin.provenance.as_reference()),
            ("invalidating_candle", observation),
            (
                "previous_candle",
                self.previous.upstream.observation if self.previous is not None else None,
            ),
            ("rejection_reason", reason),
            ("qualified", reason is None),
            ("displacement", displacement),
            (
                "displacement_reference",
                displacement.provenance.as_reference() if displacement is not None else None,
            ),
            ("mss", mss),
            ("mss_reference", mss.provenance.as_reference() if mss is not None else None),
            ("structure_context", context),
            ("structure_events", context.snapshot.events),
            ("structure_reference", context.provenance.as_reference()),
            ("preceding_sweeps", sweeps),
            ("sweep_references", tuple(s.provenance.as_reference() for s in sweeps)),
            ("concurrent_fvgs", gaps),
            ("fvg_references", tuple(g.provenance.as_reference() for g in gaps)),
            ("pd_reference", self.current.upstream.provenance.as_reference()),
            ("pd_classification", self.current.upstream.classification),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class BreakerBlock:
    """Confirmed formation only; the original Order Block is never mutated."""

    evidence: BreakerEvidence
    provenance: EvidenceProvenance
    event_id: str = field(init=False)
    symbol: str = field(init=False)
    timeframe: str = field(init=False)
    price_unit: str = field(init=False)
    direction: BreakerDirection = field(init=False)
    original_ob_id: str = field(init=False)
    original_candidate_index: int = field(init=False)
    original_candidate_timestamp: datetime = field(init=False)
    original_ob_confirmation_index: int = field(init=False)
    original_ob_confirmation_timestamp: datetime = field(init=False)
    original_ob_available_at: datetime = field(init=False)
    original_lower_boundary: Decimal = field(init=False)
    original_upper_boundary: Decimal = field(init=False)
    lower_boundary: Decimal = field(init=False)
    upper_boundary: Decimal = field(init=False)
    zone_size: Decimal = field(init=False)
    invalidation_index: int = field(init=False)
    invalidation_timestamp: datetime = field(init=False)
    invalidation_available_at: datetime = field(init=False)
    displacement_index: int = field(init=False)
    displacement_timestamp: datetime = field(init=False)
    displacement_available_at: datetime = field(init=False)
    mss_confirmation_index: int = field(init=False)
    mss_confirmation_timestamp: datetime = field(init=False)
    mss_available_at: datetime = field(init=False)
    confirmation_index: int = field(init=False)
    confirmation_timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)
    methodology_version: str = field(init=False, default=METHODOLOGY_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, BreakerEvidence) or not self.evidence.qualified:
            raise AnalysisInputError("BreakerBlock requires qualified first-violation evidence")
        item = self.evidence
        origin, observation = item.original_ob, item.invalidating_candle
        displacement, mss = item.displacement, item.mss
        assert displacement is not None and mss is not None
        _metadata(self.provenance, observation, (item.provenance.as_reference(),))
        if (
            self.provenance.source_candles != item.provenance.source_candles
            or self.provenance.configuration_hash != item.provenance.configuration_hash
            or self.provenance.input_prefix_hash != item.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "BreakerBlock provenance must match its exact qualified evidence"
            )
        for name, value in (
            ("event_id", self.provenance.evidence_id),
            ("symbol", origin.symbol),
            ("timeframe", origin.timeframe),
            ("price_unit", origin.price_unit),
            ("direction", item.direction),
            ("original_ob_id", origin.event_id),
            ("original_candidate_index", origin.candidate_index),
            ("original_candidate_timestamp", origin.candidate_timestamp),
            ("original_ob_confirmation_index", origin.confirmation_index),
            ("original_ob_confirmation_timestamp", origin.confirmation_timestamp),
            ("original_ob_available_at", origin.available_at),
            ("original_lower_boundary", origin.zone_lower_boundary),
            ("original_upper_boundary", origin.zone_upper_boundary),
            ("lower_boundary", origin.zone_lower_boundary),
            ("upper_boundary", origin.zone_upper_boundary),
            ("zone_size", origin.zone_size),
            ("invalidation_index", observation.reference.candle_index),
            ("invalidation_timestamp", observation.reference.opened_at),
            ("invalidation_available_at", observation.available_at),
            ("displacement_index", displacement.detection_index),
            ("displacement_timestamp", displacement.timestamp),
            ("displacement_available_at", displacement.available_at),
            ("mss_confirmation_index", mss.detection_index),
            ("mss_confirmation_timestamp", mss.timestamp),
            ("mss_available_at", mss.available_at),
            ("confirmation_index", observation.reference.candle_index),
            ("confirmation_timestamp", observation.reference.opened_at),
            ("available_at", observation.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class BreakerSnapshot:
    settings: BreakerBlockConfig
    upstream: MSSSnapshot
    previous: MSSSnapshot | None
    evidence: tuple[BreakerEvidence, ...]
    events: tuple[BreakerBlock, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, BreakerBlockConfig):
            raise AnalysisInputError("Breaker snapshot requires strict formation settings")
        validate_frames(self.previous, self.upstream)
        if (
            not isinstance(self.evidence, tuple)
            or not all(isinstance(e, BreakerEvidence) for e in self.evidence)
            or not isinstance(self.events, tuple)
            or not all(isinstance(e, BreakerBlock) for e in self.events)
        ):
            raise AnalysisInputError(
                "Breaker evidence and event deltas must be immutable typed tuples"
            )
        if len({e.original_ob_id for e in self.evidence}) != len(self.evidence):
            raise AnalysisInputError(
                "an OB can have only one first-violation assessment per candle"
            )
        if tuple(e.evidence for e in self.events) != tuple(e for e in self.evidence if e.qualified):
            raise AnalysisInputError(
                "Breaker events must exactly match the qualified evidence subset"
            )
        for evidence in self.evidence:
            if (
                evidence.settings != self.settings
                or evidence.previous != self.previous
                or evidence.current != self.upstream
            ):
                raise AnalysisInputError(
                    "Breaker evidence must belong to this exact assessment frame"
                )
        refs = (self.upstream.provenance.as_reference(),) + (
            (self.previous.provenance.as_reference(),) if self.previous is not None else ()
        )
        refs += tuple(e.provenance.as_reference() for e in self.evidence)
        refs += tuple(e.provenance.as_reference() for e in self.events)
        _metadata(self.provenance, self.upstream.upstream.observation, refs)
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("Breaker snapshot must reuse the current consumed prefix")
