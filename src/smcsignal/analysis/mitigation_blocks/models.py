"""Immutable first-interaction assessments of original Order Block zones."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.breaker_blocks.models import BreakerSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.mitigation_blocks.calculation import (
    MitigationDirection,
    converted_ids,
    direction_for,
    interior_overlap,
    intersects,
    known_at_open,
    observation,
    overlap_span,
    validate_frames,
)
from smcsignal.analysis.mitigation_blocks.config import METHODOLOGY_VERSION, MitigationBlockConfig
from smcsignal.analysis.mss.calculation import source_frame
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.provenance import CandleReference, EvidenceProvenance, EvidenceReference


def evidence_sources(
    previous: BreakerSnapshot | None, current: BreakerSnapshot
) -> tuple[CandleReference, ...]:
    return ((observation(previous).reference,) if previous is not None else ()) + (
        observation(current).reference,
    )


def evidence_references(
    origin: OrderBlockEvent, previous: BreakerSnapshot | None, current: BreakerSnapshot
) -> tuple[EvidenceReference, ...]:
    refs = [origin.provenance.as_reference(), current.provenance.as_reference()]
    if previous is not None:
        refs.append(previous.provenance.as_reference())
    return tuple(dict.fromkeys(refs))


@dataclass(frozen=True, slots=True)
class MitigationEvidence:
    """A first valid post-publication interior overlap with an original OB zone."""

    settings: MitigationBlockConfig
    original_ob: OrderBlockEvent
    previous: BreakerSnapshot | None
    current: BreakerSnapshot
    provenance: EvidenceProvenance
    direction: MitigationDirection = field(init=False)
    original_ob_id: str = field(init=False)
    original_ob_reference: EvidenceReference = field(init=False)
    interaction_candle: ObservedCandle = field(init=False)
    previous_candle: ObservedCandle | None = field(init=False)
    zone_lower_boundary: Decimal = field(init=False)
    zone_upper_boundary: Decimal = field(init=False)
    zone_size: Decimal = field(init=False)
    overlap_lower: Decimal = field(init=False)
    overlap_upper: Decimal = field(init=False)
    overlap_size: Decimal = field(init=False)
    body_lower: Decimal = field(init=False)
    body_upper: Decimal = field(init=False)
    body_overlap_size: Decimal = field(init=False)
    wick_overlap: bool = field(init=False)
    body_overlap: bool = field(init=False)
    full_traversal: bool = field(init=False)
    open_inside: bool = field(init=False)
    close_inside: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, MitigationBlockConfig) or not isinstance(
            self.original_ob, OrderBlockEvent
        ):
            raise AnalysisInputError(
                "Mitigation evidence requires typed settings and an original Order Block"
            )
        validate_frames(self.previous, self.current)
        current = observation(self.current)
        origin = self.original_ob
        source = source_frame(self.current.upstream.upstream)
        if (
            origin.provenance.series != current.reference.series
            or origin.price_unit != source.price_unit
            or not known_at_open(origin, current)
            or origin.available_at > current.available_at
        ):
            raise AnalysisInputError(
                "source OB must already be available in this series before the interaction opens"
            )
        if self.settings.ignore_after_breaker and origin.event_id in converted_ids(self.current):
            raise AnalysisInputError(
                "ignore_after_breaker forbids a first mitigation on a confirmed Breaker conversion"
            )
        candle = current.candle
        if not intersects(origin, candle):
            raise AnalysisInputError(
                "mitigation evidence requires positive interior overlap with the original OB zone"
            )
        _metadata(
            self.provenance, current, evidence_references(origin, self.previous, self.current)
        )
        if (
            self.provenance.source_candles != evidence_sources(self.previous, self.current)
            or self.provenance.input_prefix_hash != self.current.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "Mitigation evidence must retain actual close observations and publication prefix"
            )
        zone_low, zone_high = origin.zone_lower_boundary, origin.zone_upper_boundary
        overlap_lower, overlap_upper, overlap_size = overlap_span(
            candle.low, candle.high, zone_low, zone_high
        )
        body_low, body_high = min(candle.open, candle.close), max(candle.open, candle.close)
        body_hits = interior_overlap(body_low, body_high, zone_low, zone_high)
        body_span = overlap_span(body_low, body_high, zone_low, zone_high) if body_hits else None
        for name, value in (
            ("direction", direction_for(origin)),
            ("original_ob_id", origin.event_id),
            ("original_ob_reference", origin.provenance.as_reference()),
            ("interaction_candle", current),
            ("previous_candle", observation(self.previous) if self.previous is not None else None),
            ("zone_lower_boundary", zone_low),
            ("zone_upper_boundary", zone_high),
            ("zone_size", origin.zone_size),
            ("overlap_lower", overlap_lower),
            ("overlap_upper", overlap_upper),
            ("overlap_size", overlap_size),
            ("body_lower", body_low),
            ("body_upper", body_high),
            ("body_overlap_size", body_span[2] if body_span is not None else Decimal(0)),
            ("wick_overlap", True),
            ("body_overlap", body_hits),
            ("full_traversal", candle.low < zone_low and candle.high > zone_high),
            ("open_inside", zone_low < candle.open < zone_high),
            ("close_inside", zone_low < candle.close < zone_high),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class MitigationBlock:
    """Confirmed first-interaction evidence; the original Order Block is never mutated."""

    evidence: MitigationEvidence
    provenance: EvidenceProvenance
    event_id: str = field(init=False)
    symbol: str = field(init=False)
    timeframe: str = field(init=False)
    price_unit: str = field(init=False)
    direction: MitigationDirection = field(init=False)
    original_ob_id: str = field(init=False)
    original_candidate_index: int = field(init=False)
    original_candidate_timestamp: datetime = field(init=False)
    original_candidate_available_at: datetime = field(init=False)
    original_ob_confirmation_index: int = field(init=False)
    original_ob_confirmation_timestamp: datetime = field(init=False)
    original_ob_available_at: datetime = field(init=False)
    original_lower_boundary: Decimal = field(init=False)
    original_upper_boundary: Decimal = field(init=False)
    zone_size: Decimal = field(init=False)
    overlap_lower: Decimal = field(init=False)
    overlap_upper: Decimal = field(init=False)
    overlap_size: Decimal = field(init=False)
    interaction_index: int = field(init=False)
    interaction_timestamp: datetime = field(init=False)
    interaction_closed_at: datetime = field(init=False)
    interaction_available_at: datetime = field(init=False)
    confirmation_index: int = field(init=False)
    confirmation_timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)
    methodology_version: str = field(init=False, default=METHODOLOGY_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, MitigationEvidence):
            raise AnalysisInputError("MitigationBlock requires qualified interaction evidence")
        item = self.evidence
        origin, current = item.original_ob, item.interaction_candle
        _metadata(self.provenance, current, (item.provenance.as_reference(),))
        if (
            self.provenance.source_candles != item.provenance.source_candles
            or self.provenance.configuration_hash != item.provenance.configuration_hash
            or self.provenance.input_prefix_hash != item.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "MitigationBlock provenance must match its exact qualified evidence"
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
            ("original_candidate_available_at", origin.candidate_available_at),
            ("original_ob_confirmation_index", origin.confirmation_index),
            ("original_ob_confirmation_timestamp", origin.confirmation_timestamp),
            ("original_ob_available_at", origin.available_at),
            ("original_lower_boundary", origin.zone_lower_boundary),
            ("original_upper_boundary", origin.zone_upper_boundary),
            ("zone_size", origin.zone_size),
            ("overlap_lower", item.overlap_lower),
            ("overlap_upper", item.overlap_upper),
            ("overlap_size", item.overlap_size),
            ("interaction_index", current.reference.candle_index),
            ("interaction_timestamp", current.reference.opened_at),
            ("interaction_closed_at", current.reference.closed_at),
            ("interaction_available_at", current.available_at),
            ("confirmation_index", current.reference.candle_index),
            ("confirmation_timestamp", current.reference.opened_at),
            ("available_at", current.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class MitigationSnapshot:
    settings: MitigationBlockConfig
    upstream: BreakerSnapshot
    previous: BreakerSnapshot | None
    evidence: tuple[MitigationEvidence, ...]
    events: tuple[MitigationBlock, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, MitigationBlockConfig):
            raise AnalysisInputError("Mitigation snapshot requires strict interaction settings")
        validate_frames(self.previous, self.upstream)
        if (
            not isinstance(self.evidence, tuple)
            or not all(isinstance(e, MitigationEvidence) for e in self.evidence)
            or not isinstance(self.events, tuple)
            or not all(isinstance(e, MitigationBlock) for e in self.events)
        ):
            raise AnalysisInputError(
                "Mitigation evidence and event deltas must be immutable typed tuples"
            )
        if len({e.original_ob_id for e in self.evidence}) != len(self.evidence):
            raise AnalysisInputError(
                "an OB can have only one first-interaction assessment per candle"
            )
        if tuple(e.evidence for e in self.events) != self.evidence:
            raise AnalysisInputError("Mitigation events must exactly match the published evidence")
        for item in self.evidence:
            if (
                item.settings != self.settings
                or item.previous != self.previous
                or item.current != self.upstream
            ):
                raise AnalysisInputError(
                    "Mitigation evidence must belong to this exact assessment frame"
                )
        refs = (self.upstream.provenance.as_reference(),) + (
            (self.previous.provenance.as_reference(),) if self.previous is not None else ()
        )
        refs += tuple(e.provenance.as_reference() for e in self.evidence)
        refs += tuple(e.provenance.as_reference() for e in self.events)
        _metadata(self.provenance, observation(self.upstream), refs)
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("Mitigation snapshot must reuse the current consumed prefix")
