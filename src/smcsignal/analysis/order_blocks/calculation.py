"""Formation selection from known candle facts and existing confirmation evidence."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.calculation import validate_window
from smcsignal.analysis.fvg.models import FVGEvent, FVGSnapshot
from smcsignal.analysis.models import StructureEvent, StructureEventKind, TrendDirection
from smcsignal.analysis.order_blocks.config import (
    CandidateSelection,
    OrderBlockConfig,
    StructureRequirement,
    ZoneBasis,
)


class CandleClassification(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    DOJI = "doji"


class CandidateMatch(NamedTuple):
    frame: DisplacementSnapshot
    classification: CandleClassification
    lower: Decimal
    upper: Decimal
    size: Decimal


def validate_history(
    history: tuple[DisplacementSnapshot, ...],
    displacement_frame: DisplacementSnapshot,
    config: OrderBlockConfig,
) -> None:
    if (
        not isinstance(displacement_frame, DisplacementSnapshot)
        or not isinstance(history, tuple)
        or not all(isinstance(f, DisplacementSnapshot) for f in history)
    ):
        raise AnalysisInputError("candidate history must be an immutable Phase 5 frame tuple")
    index = displacement_frame.metrics.observation.reference.candle_index
    if len(history) != min(index, config.max_candidate_lookback):
        raise AnalysisInputError(
            "candidate search must retain the full bounded pre-displacement history"
        )
    if not history:
        return
    for previous, current in zip(history, (*history[1:], displacement_frame), strict=True):
        validate_window((previous, current))


def classification(frame: DisplacementSnapshot) -> CandleClassification:
    # Reuse Phase 5's exact own-candle direction; do not reclassify from later prices.
    direction = frame.metrics.direction
    if direction is None:
        return CandleClassification.DOJI
    return (
        CandleClassification.BULLISH
        if direction == TrendDirection.BULLISH
        else CandleClassification.BEARISH
    )


def zone(frame: DisplacementSnapshot, basis: ZoneBasis) -> tuple[Decimal, Decimal, Decimal]:
    candle = frame.metrics.observation.candle
    if basis == ZoneBasis.BODY:
        return (
            min(candle.open, candle.close),
            max(candle.open, candle.close),
            frame.metrics.body_size,
        )
    return candle.low, candle.high, frame.metrics.range_size


def select_candidate(
    history: tuple[DisplacementSnapshot, ...],
    displacement_frame: DisplacementSnapshot,
    config: OrderBlockConfig,
) -> CandidateMatch | None:
    if not displacement_frame.events:
        return None
    displacement = displacement_frame.events[0]
    opposite = (
        CandleClassification.BEARISH
        if displacement.direction == TrendDirection.BULLISH
        else CandleClassification.BULLISH
    )
    ordered = (
        reversed(history)
        if config.candidate_selection == CandidateSelection.NEAREST
        else iter(history)
    )
    for frame in ordered:
        kind = classification(frame)
        if kind != opposite and not (config.allow_doji and kind == CandleClassification.DOJI):
            continue
        if frame.metrics.observation.available_at > displacement.observation.reference.opened_at:
            continue
        lower, upper, size = zone(frame, config.zone_basis)
        if size > 0:
            return CandidateMatch(frame, kind, lower, upper, size)
    return None


def matching_structure(displacement_frame: DisplacementSnapshot) -> StructureEvent | None:
    if not displacement_frame.events:
        return None
    direction = displacement_frame.events[0].direction
    return next(
        (
            event
            for event in displacement_frame.liquidity.context.snapshot.events
            if event.direction == direction
        ),
        None,
    )


def formation_candidate(
    history: tuple[DisplacementSnapshot, ...],
    displacement_frame: DisplacementSnapshot,
    config: OrderBlockConfig,
) -> CandidateMatch | None:
    candidate = select_candidate(history, displacement_frame, config)
    if candidate is None:
        return None
    displacement = displacement_frame.events[0]
    # Select from own-candle facts FIRST. Never search for a more favorable old zone
    # after the selected candidate fails the confirming close/departure check.
    close = displacement.observation.candle.close
    if (displacement.direction == TrendDirection.BULLISH and close <= candidate.upper) or (
        displacement.direction == TrendDirection.BEARISH and close >= candidate.lower
    ):
        return None
    structure = matching_structure(displacement_frame)
    requirement = config.structure_requirement
    if requirement == StructureRequirement.DISPLACEMENT_ONLY:
        return candidate
    if structure is None:
        return None
    if requirement == StructureRequirement.BOS and structure.kind != StructureEventKind.BOS:
        return None
    if requirement == StructureRequirement.CHOCH and structure.kind != StructureEventKind.CHOCH:
        return None
    return candidate


def matching_fvg(
    displacement_frame: DisplacementSnapshot, publication: FVGSnapshot
) -> FVGEvent | None:
    if not displacement_frame.events:
        return None
    displacement = displacement_frame.events[0]
    for gap in publication.events:
        if (
            gap.detection_index == displacement.detection_index + 1
            and gap.direction == displacement.direction
            and gap.associated_displacement is not None
            and gap.associated_displacement.event_id == displacement.event_id
            and gap.c2.reference.candle_index == displacement.detection_index
            and gap.available_at <= publication.provenance.available_at
        ):
            return gap
    return None
