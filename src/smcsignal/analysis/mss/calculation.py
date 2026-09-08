"""Qualify existing opposing structure breaks; never detect a second BOS/CHoCH."""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from smcsignal.analysis.displacement.models import DisplacementEvent, DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.calculation import validate_window
from smcsignal.analysis.fvg.models import FVGEvent
from smcsignal.analysis.liquidity.models import LiquidityPool, SwingEvidence
from smcsignal.analysis.models import StructureEvent, StructureEventKind, SwingKind, TrendDirection
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.premium_discount.models import PDArrayContext, PDSnapshot


class MSSDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class ShiftMatch(NamedTuple):
    direction: MSSDirection
    displacement: DisplacementEvent
    structure_break: StructureEvent
    broken_level: SwingEvidence


class Relationships(NamedTuple):
    broken_level_pools: tuple[LiquidityPool, ...]
    swept_pools: tuple[LiquidityPool, ...]
    concurrent_fvgs: tuple[FVGEvent, ...]
    displacement_order_blocks: tuple[OrderBlockEvent, ...]
    pd_arrays: tuple[PDArrayContext, ...]


def source_frame(frame: PDSnapshot) -> DisplacementSnapshot:
    return frame.upstream.upstream.upstream


def validate_pair(previous: PDSnapshot | None, current: PDSnapshot) -> None:
    if not isinstance(current, PDSnapshot):
        raise AnalysisInputError("MSS consumes existing PDSnapshot frames, not raw data")
    if previous is None:
        if current.observation.reference.candle_index != 0:
            raise AnalysisInputError("MSS history must start at observed index zero")
        return
    if not isinstance(previous, PDSnapshot):
        raise AnalysisInputError("MSS previous context must be an existing PD frame")
    validate_window((source_frame(previous), source_frame(current)))
    for old, new in (
        (previous, current),
        (previous.upstream, current.upstream),
        (previous.upstream.upstream, current.upstream.upstream),
    ):
        if (
            old.provenance.configuration_hash != new.provenance.configuration_hash
            or old.settings != new.settings
        ):
            raise AnalysisInputError(
                "upstream PD/OB/FVG configuration cannot change during MSS replay"
            )


def qualifying_shift(previous: PDSnapshot | None, current: PDSnapshot) -> ShiftMatch | None:
    if previous is None:
        return None
    before = source_frame(previous).liquidity.context
    now = source_frame(current)
    control = before.snapshot.trend
    if not control.ready or control.direction == TrendDirection.RANGING:
        return None
    if before.provenance.available_at > current.observation.reference.opened_at:
        return None
    if not now.events:
        return None
    displacement = now.events[0]
    if displacement.direction == control.direction:
        return None  # continuation displacement/BOS is not a reversal warning
    structure = next(
        (
            e
            for e in now.liquidity.context.snapshot.events
            if e.kind == StructureEventKind.CHOCH
            and e.direction == displacement.direction
            and e.trend_before == control.direction
        ),
        None,
    )
    if structure is None:
        return None
    bullish = displacement.direction == TrendDirection.BULLISH
    level = previous.latest_high if bullish else previous.latest_low
    if (
        level is None
        or level.swing != structure.level
        or level.swing.kind != (SwingKind.HIGH if bullish else SwingKind.LOW)
    ):
        return None
    if (
        level.swing.confirmed_index >= current.observation.reference.candle_index
        or level.provenance.available_at > current.observation.reference.opened_at
    ):
        return None
    if (
        structure.previous_close != previous.observation.candle.close
        or structure.close != displacement.observation.candle.close
    ):
        raise AnalysisInputError(
            "existing structure break contradicts the actual closed observations"
        )
    return ShiftMatch(
        MSSDirection.BULLISH if bullish else MSSDirection.BEARISH, displacement, structure, level
    )


def relationships(current: PDSnapshot, match: ShiftMatch) -> Relationships:
    source = source_frame(current)
    pools = tuple(
        p
        for p in source.liquidity.pool_updates
        if any(m.swing == match.structure_break.level for m in p.members)
    )
    swept = tuple(dict.fromkeys(s.pool for s in match.displacement.preceding_sweeps))
    # Co-publication context, NOT an FVG attributed to the current displacement (its C3 is future).
    gaps = tuple(
        g for g in current.upstream.upstream.events if g.direction == match.displacement.direction
    )
    blocks = tuple(
        b
        for b in current.upstream.events
        if b.displacement.event_id == match.displacement.event_id
        and b.direction == match.displacement.direction
        and b.confirmation_index == current.observation.reference.candle_index
    )
    ids = {
        match.displacement.event_id,
        *(p.provenance.evidence_id for p in (*pools, *swept)),
        *(s.sweep_id for s in match.displacement.preceding_sweeps),
        *(g.event_id for g in gaps),
        *(b.event_id for b in blocks),
    }
    pd_arrays = tuple(a for a in current.arrays if a.source_reference.evidence_id in ids)
    return Relationships(pools, swept, gaps, blocks, pd_arrays)
