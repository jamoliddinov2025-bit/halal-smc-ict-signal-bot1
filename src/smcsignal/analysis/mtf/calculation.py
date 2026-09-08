"""Causal HTF eligibility, independent labels, and unweighted confluence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.provenance import EvidenceProvenance, _instant


class MTFDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


class MTFEvidenceKind(StrEnum):
    STRUCTURE_CONTEXT = "structure_context"
    SWING = "swing"
    LIQUIDITY_POOL = "liquidity_pool"
    SWEEP = "sweep"
    DISPLACEMENT = "displacement"
    FVG = "fvg"
    ORDER_BLOCK = "order_block"
    DEALING_RANGE = "dealing_range"
    EQUILIBRIUM = "equilibrium"
    PD_ARRAY = "pd_array"
    PD_SNAPSHOT = "pd_snapshot"
    OTE_ZONE = "ote_zone"
    OTE_OBSERVATION = "ote_observation"
    OTE_SNAPSHOT = "ote_snapshot"


KIND_RANK: dict[MTFEvidenceKind, int] = {kind: index for index, kind in enumerate(MTFEvidenceKind)}


def nested_displacement(frame: OTESnapshot) -> DisplacementSnapshot:
    return frame.upstream.upstream.upstream.upstream


def primary_opened_at(frame: OTESnapshot) -> datetime:
    return frame.upstream.observation.reference.opened_at


def is_eligible(available_at: datetime, opened_at: datetime) -> bool:
    """HTF evidence may be used only if it was already knowable at the LTF open."""
    return _instant(available_at, "available_at") <= _instant(opened_at, "opened_at")


def htf_direction(frame: OTESnapshot | None) -> tuple[MTFDirection, str | None]:
    if frame is None:
        return MTFDirection.INSUFFICIENT_CONTEXT, "no_completed_htf_candle"
    trend = nested_displacement(frame).liquidity.context.snapshot.trend
    if not trend.ready:
        return MTFDirection.INSUFFICIENT_CONTEXT, "htf_structure_not_ready"
    if trend.direction is TrendDirection.BULLISH:
        return MTFDirection.BULLISH, None
    if trend.direction is TrendDirection.BEARISH:
        return MTFDirection.BEARISH, None
    if trend.direction is TrendDirection.RANGING:
        return MTFDirection.NEUTRAL, None
    raise AnalysisInputError("HTF trend direction is not a supported Phase 3 label")


def confluence(directions: tuple[MTFDirection, ...]) -> MTFDirection:
    """Unweighted agreement. Independent HTF states are not collapsed beforehand."""
    known = tuple(item for item in directions if item is not MTFDirection.INSUFFICIENT_CONTEXT)
    if not known:
        return MTFDirection.INSUFFICIENT_CONTEXT
    first = known[0]
    if all(item is first for item in known):
        return first
    return MTFDirection.MIXED


def publication_records(
    frame: OTESnapshot,
) -> tuple[tuple[MTFEvidenceKind, EvidenceProvenance], ...]:
    pd = frame.upstream
    ob = pd.upstream
    fvg = ob.upstream
    displacement = fvg.upstream
    liquidity = displacement.liquidity
    items: list[tuple[MTFEvidenceKind, EvidenceProvenance]] = [
        *((MTFEvidenceKind.SWING, item.provenance) for item in liquidity.confirmed_swings),
        *((MTFEvidenceKind.LIQUIDITY_POOL, item.provenance) for item in liquidity.pool_updates),
        *((MTFEvidenceKind.SWEEP, item.provenance) for item in liquidity.sweeps),
        *((MTFEvidenceKind.DISPLACEMENT, item.provenance) for item in displacement.events),
        *((MTFEvidenceKind.FVG, item.provenance) for item in fvg.events),
        *((MTFEvidenceKind.ORDER_BLOCK, item.provenance) for item in ob.events),
        *((MTFEvidenceKind.PD_ARRAY, item.provenance) for item in pd.arrays),
    ]
    observation_index = pd.observation.reference.candle_index
    if pd.dealing_range is not None and pd.dealing_range.confirmation_index == observation_index:
        items.append((MTFEvidenceKind.DEALING_RANGE, pd.dealing_range.provenance))
        if pd.equilibrium is not None:
            items.append((MTFEvidenceKind.EQUILIBRIUM, pd.equilibrium.provenance))
    if frame.zone is not None and frame.zone.confirmation_index == observation_index:
        items.append((MTFEvidenceKind.OTE_ZONE, frame.zone.provenance))
    return tuple(items)


def state_records(frame: OTESnapshot) -> tuple[tuple[MTFEvidenceKind, EvidenceProvenance], ...]:
    pd = frame.upstream
    context = nested_displacement(frame).liquidity.context
    items: list[tuple[MTFEvidenceKind, EvidenceProvenance]] = [
        (MTFEvidenceKind.OTE_SNAPSHOT, frame.provenance),
        (MTFEvidenceKind.OTE_OBSERVATION, frame.observation.provenance),
        (MTFEvidenceKind.PD_SNAPSHOT, pd.provenance),
        (MTFEvidenceKind.STRUCTURE_CONTEXT, context.provenance),
    ]
    if frame.zone is not None:
        items.append((MTFEvidenceKind.OTE_ZONE, frame.zone.provenance))
    if pd.dealing_range is not None:
        items.append((MTFEvidenceKind.DEALING_RANGE, pd.dealing_range.provenance))
    if pd.equilibrium is not None:
        items.append((MTFEvidenceKind.EQUILIBRIUM, pd.equilibrium.provenance))
    return tuple(items)
