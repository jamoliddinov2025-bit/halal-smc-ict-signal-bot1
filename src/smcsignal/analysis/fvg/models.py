"""Immutable FVG creation evidence; no mutable open/fill/invalidation state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.displacement.models import DisplacementEvent, DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.calculation import middle_displacement, qualified_gap, validate_window
from smcsignal.analysis.fvg.config import METHODOLOGY_VERSION, FVGConfig
from smcsignal.analysis.liquidity.models import ObservedCandle, SweepEvent, _metadata
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference


def related_references(window: tuple[DisplacementSnapshot, ...]) -> tuple[EvidenceReference, ...]:
    """Copy the middle candle's existing context; never select again at C3."""
    references = tuple(frame.provenance.as_reference() for frame in window)
    if len(window) == 3:
        displacement = middle_displacement(window)
        if displacement is not None:
            references += (displacement.provenance.as_reference(),)
        references += tuple(s.provenance.as_reference() for s in window[1].preceding_sweeps)
    return references


@dataclass(frozen=True, slots=True)
class FVGEvent:
    """One permanent three-candle formation, first knowable after C3 closes."""

    settings: FVGConfig
    window: tuple[DisplacementSnapshot, ...]
    provenance: EvidenceProvenance
    event_id: str = field(init=False)
    symbol: str = field(init=False)
    timeframe: str = field(init=False)
    price_unit: str = field(init=False)
    direction: TrendDirection = field(init=False)
    creation_index: int = field(init=False)
    detection_index: int = field(init=False)
    timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)
    c1: ObservedCandle = field(init=False)
    c2: ObservedCandle = field(init=False)
    c3: ObservedCandle = field(init=False)
    lower_boundary: Decimal = field(init=False)
    upper_boundary: Decimal = field(init=False)
    gap_size: Decimal = field(init=False)
    associated_displacement: DisplacementEvent | None = field(init=False)
    displacement_reference: EvidenceReference | None = field(init=False)
    displacement_aligned: bool | None = field(init=False)
    preceding_sweeps: tuple[SweepEvent, ...] = field(init=False)
    preceding_sweep_references: tuple[EvidenceReference, ...] = field(init=False)
    threshold_version: str = field(init=False, default=METHODOLOGY_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, FVGConfig):
            raise AnalysisInputError("FVG event requires typed settings")
        validate_window(self.window)
        values = qualified_gap(self.window, self.settings)
        if values is None:
            raise AnalysisInputError(
                "FVG event must satisfy strict three-candle geometry and configured filters"
            )
        first, middle, last = self.window
        c1, c2, c3 = (frame.metrics.observation for frame in self.window)
        direction, lower, upper, size = values
        displacement = middle_displacement(self.window)
        if displacement is not None and (
            displacement.detection_index != c2.reference.candle_index
            or displacement.available_at > c3.available_at
            or displacement.provenance.series != c3.reference.series
        ):
            raise AnalysisInputError("associated displacement must be the already-known C2 event")
        # Phase 5 has already selected/validated this group using C2's pre-open cutoff.
        # Copy it intact even when C2 itself did not qualify as displacement.
        sweeps = middle.preceding_sweeps
        _metadata(self.provenance, c3, related_references(self.window))
        if (
            self.provenance.source_candles != (c1.reference, c2.reference, c3.reference)
            or self.provenance.input_prefix_hash != last.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "FVG provenance must retain exactly C1/C2/C3 and the C3 input prefix"
            )
        for name, value in (
            ("event_id", self.provenance.evidence_id),
            ("symbol", last.provenance.series.symbol),
            ("timeframe", last.provenance.series.timeframe),
            ("price_unit", last.price_unit),
            ("direction", direction),
            ("creation_index", c3.reference.candle_index),
            ("detection_index", c3.reference.candle_index),
            ("timestamp", c3.reference.opened_at),
            ("available_at", c3.available_at),
            ("c1", c1),
            ("c2", c2),
            ("c3", c3),
            ("lower_boundary", lower),
            ("upper_boundary", upper),
            ("gap_size", size),
            ("associated_displacement", displacement),
            (
                "displacement_reference",
                displacement.provenance.as_reference() if displacement is not None else None,
            ),
            (
                "displacement_aligned",
                displacement.direction == direction if displacement is not None else None,
            ),
            ("preceding_sweeps", sweeps),
            ("preceding_sweep_references", tuple(s.provenance.as_reference() for s in sweeps)),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class FVGSnapshot:
    """One creation-assessment frame per input; no active-zone/lifecycle registry."""

    settings: FVGConfig
    window: tuple[DisplacementSnapshot, ...]
    events: tuple[FVGEvent, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, FVGConfig):
            raise AnalysisInputError("FVG snapshot requires typed settings")
        validate_window(self.window)
        last = self.window[-1]
        observation = last.metrics.observation
        if len(self.window) != min(observation.reference.candle_index + 1, 3):
            raise AnalysisInputError("FVG window readiness must match fixed-origin history")
        if (
            not isinstance(self.events, tuple)
            or len(self.events) > 1
            or not all(isinstance(e, FVGEvent) for e in self.events)
        ):
            raise AnalysisInputError(
                "FVG creation deltas must be an immutable tuple of zero or one event"
            )
        if bool(self.events) != (qualified_gap(self.window, self.settings) is not None):
            raise AnalysisInputError(
                "FVG events must exactly match the configured geometry and filter"
            )
        for event in self.events:
            if event.settings != self.settings or event.window != self.window:
                raise AnalysisInputError("FVG event must belong to this exact three-candle window")
        dependencies = related_references(self.window) + tuple(
            e.provenance.as_reference() for e in self.events
        )
        _metadata(self.provenance, observation, dependencies)
        if (
            self.provenance.source_candles
            != tuple(f.metrics.observation.reference for f in self.window)
            or self.provenance.input_prefix_hash != last.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "FVG snapshot must retain its exact window and current input prefix"
            )

    @property
    def upstream(self) -> DisplacementSnapshot:
        return self.window[-1]
