"""Immutable OB formation evidence, with candidate and publication times separate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.displacement.models import DisplacementEvent, DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.calculation import validate_window
from smcsignal.analysis.fvg.models import FVGEvent, FVGSnapshot
from smcsignal.analysis.liquidity.models import (
    ObservedCandle,
    StructureContext,
    SweepEvent,
    _metadata,
)
from smcsignal.analysis.models import StructureEvent, TrendDirection
from smcsignal.analysis.order_blocks.calculation import (
    CandleClassification,
    formation_candidate,
    matching_fvg,
    matching_structure,
    validate_history,
)
from smcsignal.analysis.order_blocks.config import METHODOLOGY_VERSION, OrderBlockConfig
from smcsignal.analysis.provenance import CandleReference, EvidenceProvenance, EvidenceReference


def formation_references(
    history: tuple[DisplacementSnapshot, ...],
    displacement_frame: DisplacementSnapshot,
    publication: FVGSnapshot,
    gap: FVGEvent | None,
) -> tuple[EvidenceReference, ...]:
    displacement = displacement_frame.events[0]
    refs = [
        *(f.provenance.as_reference() for f in history),
        displacement_frame.provenance.as_reference(),
        displacement.provenance.as_reference(),
        publication.provenance.as_reference(),
    ]
    if matching_structure(displacement_frame) is not None:
        refs.append(displacement_frame.liquidity.context.provenance.as_reference())
    refs.extend(s.provenance.as_reference() for s in displacement.preceding_sweeps)
    if gap is not None:
        refs.append(gap.provenance.as_reference())
    return tuple(refs)


def formation_sources(
    history: tuple[DisplacementSnapshot, ...],
    displacement_frame: DisplacementSnapshot,
    publication: FVGSnapshot,
) -> tuple[CandleReference, ...]:
    sources = (
        *[f.metrics.observation.reference for f in history],
        displacement_frame.metrics.observation.reference,
    )
    current = publication.upstream.metrics.observation.reference
    return sources if current == sources[-1] else (*sources, current)


@dataclass(frozen=True, slots=True)
class OrderBlockEvent:
    settings: OrderBlockConfig
    candidate_window: tuple[DisplacementSnapshot, ...]
    displacement_frame: DisplacementSnapshot
    publication: FVGSnapshot
    provenance: EvidenceProvenance
    event_id: str = field(init=False)
    symbol: str = field(init=False)
    timeframe: str = field(init=False)
    price_unit: str = field(init=False)
    direction: TrendDirection = field(init=False)
    candidate: ObservedCandle = field(init=False)
    candidate_frame: DisplacementSnapshot = field(init=False)
    candidate_classification: CandleClassification = field(init=False)
    candidate_index: int = field(init=False)
    candidate_timestamp: datetime = field(init=False)
    candidate_available_at: datetime = field(init=False)
    candidate_distance: int = field(init=False)
    zone_lower_boundary: Decimal = field(init=False)
    zone_upper_boundary: Decimal = field(init=False)
    zone_size: Decimal = field(init=False)
    displacement: DisplacementEvent = field(init=False)
    displacement_reference: EvidenceReference = field(init=False)
    displacement_index: int = field(init=False)
    displacement_timestamp: datetime = field(init=False)
    displacement_available_at: datetime = field(init=False)
    structure_event: StructureEvent | None = field(init=False)
    structure_context: StructureContext = field(init=False)
    structure_reference: EvidenceReference | None = field(init=False)
    structure_confirmation_index: int | None = field(init=False)
    structure_confirmation_timestamp: datetime | None = field(init=False)
    structure_available_at: datetime | None = field(init=False)
    preceding_sweeps: tuple[SweepEvent, ...] = field(init=False)
    preceding_sweep_references: tuple[EvidenceReference, ...] = field(init=False)
    associated_fvg: FVGEvent | None = field(init=False)
    fvg_reference: EvidenceReference | None = field(init=False)
    fvg_confirmation_index: int | None = field(init=False)
    fvg_confirmation_timestamp: datetime | None = field(init=False)
    fvg_available_at: datetime | None = field(init=False)
    confirmation_index: int = field(init=False)
    confirmation_timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)
    threshold_version: str = field(init=False, default=METHODOLOGY_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, OrderBlockConfig) or not isinstance(
            self.publication, FVGSnapshot
        ):
            raise AnalysisInputError("OB requires typed settings and an existing publication frame")
        validate_history(self.candidate_window, self.displacement_frame, self.settings)
        candidate = formation_candidate(
            self.candidate_window, self.displacement_frame, self.settings
        )
        if candidate is None:
            raise AnalysisInputError(
                "OB requires candidate, displacement, zone departure, and configured structure"
            )
        displacement = self.displacement_frame.events[0]
        observed = self.publication.upstream.metrics.observation
        gap = None
        if self.settings.require_fvg:
            validate_window((self.displacement_frame, self.publication.upstream))
            gap = matching_fvg(self.displacement_frame, self.publication)
            if gap is None:
                raise AnalysisInputError(
                    "OB requires the next-candle matching FVG for its exact displacement"
                )
        elif self.publication.upstream != self.displacement_frame:
            raise AnalysisInputError(
                "without FVG requirement OB must publish on the displacement candle"
            )
        structure = matching_structure(self.displacement_frame)
        context = self.displacement_frame.liquidity.context
        _metadata(
            self.provenance,
            observed,
            formation_references(
                self.candidate_window, self.displacement_frame, self.publication, gap
            ),
        )
        if (
            self.provenance.source_candles
            != formation_sources(self.candidate_window, self.displacement_frame, self.publication)
            or self.provenance.input_prefix_hash != self.publication.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "OB provenance must retain exact search/confirmation candles and publication prefix"
            )
        if structure is not None and (
            structure.candle_index != displacement.detection_index
            or context.provenance.available_at > observed.available_at
        ):
            raise AnalysisInputError(
                "structure must be the known matching event on the displacement candle"
            )
        c = candidate.frame.metrics.observation
        for name, value in (
            ("event_id", self.provenance.evidence_id),
            ("symbol", self.provenance.series.symbol),
            ("timeframe", self.provenance.series.timeframe),
            ("price_unit", self.displacement_frame.price_unit),
            ("direction", displacement.direction),
            ("candidate", c),
            ("candidate_frame", candidate.frame),
            ("candidate_classification", candidate.classification),
            ("candidate_index", c.reference.candle_index),
            ("candidate_timestamp", c.reference.opened_at),
            ("candidate_available_at", c.available_at),
            ("candidate_distance", displacement.detection_index - c.reference.candle_index),
            ("zone_lower_boundary", candidate.lower),
            ("zone_upper_boundary", candidate.upper),
            ("zone_size", candidate.size),
            ("displacement", displacement),
            ("displacement_reference", displacement.provenance.as_reference()),
            ("displacement_index", displacement.detection_index),
            ("displacement_timestamp", displacement.timestamp),
            ("displacement_available_at", displacement.available_at),
            ("structure_event", structure),
            ("structure_context", context),
            (
                "structure_reference",
                context.provenance.as_reference() if structure is not None else None,
            ),
            (
                "structure_confirmation_index",
                structure.candle_index if structure is not None else None,
            ),
            (
                "structure_confirmation_timestamp",
                structure.timestamp if structure is not None else None,
            ),
            (
                "structure_available_at",
                context.provenance.available_at if structure is not None else None,
            ),
            ("preceding_sweeps", displacement.preceding_sweeps),
            ("preceding_sweep_references", displacement.preceding_sweep_references),
            ("associated_fvg", gap),
            ("fvg_reference", gap.provenance.as_reference() if gap is not None else None),
            ("fvg_confirmation_index", gap.detection_index if gap is not None else None),
            ("fvg_confirmation_timestamp", gap.timestamp if gap is not None else None),
            ("fvg_available_at", gap.available_at if gap is not None else None),
            ("confirmation_index", observed.reference.candle_index),
            ("confirmation_timestamp", observed.reference.opened_at),
            ("available_at", observed.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class OrderBlockSnapshot:
    settings: OrderBlockConfig
    upstream: FVGSnapshot
    events: tuple[OrderBlockEvent, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, OrderBlockConfig) or not isinstance(
            self.upstream, FVGSnapshot
        ):
            raise AnalysisInputError("OB snapshot requires typed settings and existing FVG frame")
        if (
            not isinstance(self.events, tuple)
            or len(self.events) > 1
            or not all(isinstance(e, OrderBlockEvent) for e in self.events)
        ):
            raise AnalysisInputError(
                "OB formation deltas must be an immutable tuple of zero or one event"
            )
        for event in self.events:
            if event.settings != self.settings or event.publication != self.upstream:
                raise AnalysisInputError("OB event must belong to this exact publication frame")
        required = (
            self.upstream.provenance.as_reference(),
            *(e.provenance.as_reference() for e in self.events),
        )
        _metadata(self.provenance, self.upstream.upstream.metrics.observation, required)
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("OB snapshot must reuse the current upstream prefix")
