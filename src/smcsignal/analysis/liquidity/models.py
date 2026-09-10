"""Immutable raw liquidity/sweep facts, with the approved provenance contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.config import LiquidityConfig, price_band
from smcsignal.analysis.models import AnalysisSnapshot, Swing, SwingKind
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    _instant,
    _text,
)
from smcsignal.data import OHLCV


class LiquiditySide(StrEnum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class LiquidityKind(StrEnum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"


class PoolStatus(StrEnum):
    ACTIVE = "active"
    SWEPT = "swept"
    INVALIDATED = "invalidated"


class InvalidationReason(StrEnum):
    NOT_KNOWN_AT_OPEN = "not_known_at_open"
    STARTED_OUTSIDE = "started_outside"
    NOT_RECLAIMED = "not_reclaimed"


@dataclass(frozen=True, slots=True)
class ObservedCandle:
    candle: OHLCV
    reference: CandleReference
    available_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.candle, OHLCV) or not isinstance(self.reference, CandleReference):
            raise AnalysisInputError("observation requires OHLCV and CandleReference")
        if self.candle.timestamp != self.reference.opened_at:
            raise AnalysisInputError("candle opening time differs from its reference")
        object.__setattr__(self, "available_at", _instant(self.available_at, "available_at"))
        if self.available_at < self.reference.closed_at:
            raise AnalysisInputError("observation is not available before its candle closes")


def _metadata(
    value: EvidenceProvenance,
    observation: ObservedCandle,
    dependencies: tuple[EvidenceReference, ...] = (),
) -> None:
    if not isinstance(value, EvidenceProvenance) or (
        value.series != observation.reference.series
        or value.available_at != observation.available_at
    ):
        raise AnalysisInputError("provenance must match the observation's series and availability")
    if observation.reference not in value.source_candles:
        raise AnalysisInputError("provenance must include the current source candle")
    if not set(dependencies).issubset(value.dependencies):
        raise AnalysisInputError("provenance is missing required snapshot dependencies")


@dataclass(frozen=True, slots=True)
class SwingEvidence:
    """Actual Phase 3 swing plus every raw candle in its confirmation window."""

    swing: Swing
    window: tuple[ObservedCandle, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.swing, Swing) or not isinstance(self.window, tuple):
            raise AnalysisInputError("swing evidence requires a Swing and immutable window")
        radius = self.swing.confirmed_index - self.swing.pivot_index
        if len(self.window) != 2 * radius + 1 or not all(
            isinstance(c, ObservedCandle) for c in self.window
        ):
            raise AnalysisInputError("swing evidence must retain its complete symmetric window")
        pivot, confirmation = self.window[radius], self.window[-1]
        if (pivot.reference.candle_index, pivot.reference.opened_at) != (
            self.swing.pivot_index,
            self.swing.pivot_timestamp,
        ):
            raise AnalysisInputError("pivot does not match its raw observation")
        if (confirmation.reference.candle_index, confirmation.reference.opened_at) != (
            self.swing.confirmed_index,
            self.swing.confirmed_timestamp,
        ):
            raise AnalysisInputError("confirmation does not match its raw observation")
        indices = tuple(c.reference.candle_index for c in self.window)
        if indices != tuple(range(indices[0], indices[0] + len(indices))):
            raise AnalysisInputError("swing window must contain consecutive observed indices")
        attribute = "high" if self.swing.kind == SwingKind.HIGH else "low"
        prices = tuple(getattr(c.candle, attribute) for c in self.window)
        if self.swing.price != prices[radius] or any(
            (
                price >= self.swing.price
                if self.swing.kind == SwingKind.HIGH
                else price <= self.swing.price
            )
            for index, price in enumerate(prices)
            if index != radius
        ):
            raise AnalysisInputError("raw window must prove the strict swing extremum")
        _metadata(self.provenance, confirmation)
        if self.provenance.source_candles != tuple(c.reference for c in self.window) or any(
            c.available_at > self.provenance.available_at for c in self.window
        ):
            raise AnalysisInputError("swing source window is incomplete or not yet available")

    @property
    def pivot(self) -> ObservedCandle:
        return self.window[len(self.window) // 2]

    @property
    def confirmation(self) -> ObservedCandle:
        return self.window[-1]


@dataclass(frozen=True, slots=True)
class StructureContext:
    observation: ObservedCandle
    snapshot: AnalysisSnapshot
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.observation, ObservedCandle) or not isinstance(
            self.snapshot, AnalysisSnapshot
        ):
            raise AnalysisInputError("context requires an observation and AnalysisSnapshot")
        if (self.snapshot.candle_index, self.snapshot.timestamp) != (
            self.observation.reference.candle_index,
            self.observation.reference.opened_at,
        ):
            raise AnalysisInputError("structure context must belong to this candle")
        _metadata(self.provenance, self.observation)


def _breached(side: LiquiditySide, lower: Decimal, upper: Decimal, candle: OHLCV) -> bool:
    return candle.high > upper if side == LiquiditySide.BUY_SIDE else candle.low < lower


def _failure(
    side: LiquiditySide,
    lower: Decimal,
    upper: Decimal,
    known_at: datetime,
    previous: ObservedCandle,
    current: ObservedCandle,
) -> InvalidationReason | None:
    if known_at > current.reference.opened_at:
        return InvalidationReason.NOT_KNOWN_AT_OPEN
    before, bar = previous.candle, current.candle
    if side == LiquiditySide.BUY_SIDE:
        started_outside = before.close > upper or bar.open > upper
        reclaimed = bar.close < lower
    else:
        started_outside = before.close < lower or bar.open < lower
        reclaimed = bar.close > upper
    if started_outside:
        return InvalidationReason.STARTED_OUTSIDE
    return None if reclaimed else InvalidationReason.NOT_RECLAIMED


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    """One fixed-anchor pool version, never a mutable latest-level container."""

    pool_id: str
    settings: LiquidityConfig
    members: tuple[SwingEvidence, ...]
    status: PoolStatus
    context: StructureContext
    provenance: EvidenceProvenance
    previous_snapshot: EvidenceReference | None = None
    transition: ObservedCandle | None = None
    previous_candle: ObservedCandle | None = None
    invalidation_reason: InvalidationReason | None = None
    sweep_reference: EvidenceReference | None = None
    side: LiquiditySide = field(init=False)
    kind: LiquidityKind = field(init=False)
    reference_price: Decimal = field(init=False)
    lower_bound: Decimal = field(init=False)
    upper_bound: Decimal = field(init=False)
    touch_count: int = field(init=False)

    def __post_init__(self) -> None:
        _text(self.pool_id, "pool_id")
        if not isinstance(self.settings, LiquidityConfig) or not isinstance(
            self.status, PoolStatus
        ):
            raise AnalysisInputError("pool requires typed settings and status")
        if (
            not isinstance(self.members, tuple)
            or not self.members
            or not all(isinstance(m, SwingEvidence) for m in self.members)
        ):
            raise AnalysisInputError("pool members must be a nonempty immutable evidence tuple")
        if not isinstance(self.context, StructureContext):
            raise AnalysisInputError("pool requires immutable historical context")
        first = self.members[0]
        side = (
            LiquiditySide.BUY_SIDE
            if first.swing.kind == SwingKind.HIGH
            else LiquiditySide.SELL_SIDE
        )
        kinds = (
            (LiquidityKind.SWING_HIGH, LiquidityKind.EQUAL_HIGHS)
            if side == LiquiditySide.BUY_SIDE
            else (LiquidityKind.SWING_LOW, LiquidityKind.EQUAL_LOWS)
        )
        lower, upper = price_band(first.swing.price, self.settings)
        for name, value in (
            ("side", side),
            ("kind", kinds[len(self.members) > 1]),
            ("reference_price", first.swing.price),
            ("lower_bound", lower),
            ("upper_bound", upper),
            ("touch_count", len(self.members)),
        ):
            object.__setattr__(self, name, value)
        previous: SwingEvidence | None = None
        for member in self.members:
            if member.swing.kind != first.swing.kind or not lower <= member.swing.price <= upper:
                raise AnalysisInputError("members must share a side and the original fixed band")
            if (
                member.provenance.series != self.context.provenance.series
                or member.provenance.available_at > self.context.provenance.available_at
            ):
                raise AnalysisInputError("member is from another series or is not yet available")
            if previous is not None and (
                member.swing.pivot_index <= previous.swing.pivot_index
                or member.swing.confirmed_index <= previous.swing.confirmed_index
            ):
                raise AnalysisInputError("pool membership must be chronological and unique")
            previous = member
        dependencies = [
            self.context.provenance.as_reference(),
            *(m.provenance.as_reference() for m in self.members),
        ]
        for reference in (self.previous_snapshot, self.sweep_reference):
            if reference is not None:
                if (
                    not isinstance(reference, EvidenceReference)
                    or reference.series != self.context.provenance.series
                ):
                    raise AnalysisInputError("pool dependencies must reference the same series")
                dependencies.append(reference)
        _metadata(self.provenance, self.context.observation, tuple(dependencies))
        if self.status == PoolStatus.ACTIVE:
            if any(
                value is not None
                for value in (
                    self.transition,
                    self.previous_candle,
                    self.invalidation_reason,
                    self.sweep_reference,
                )
            ):
                raise AnalysisInputError("active pool cannot contain a terminal transition")
        else:
            if (
                not isinstance(self.transition, ObservedCandle)
                or not isinstance(self.previous_candle, ObservedCandle)
                or self.previous_snapshot is None
            ):
                raise AnalysisInputError(
                    "terminal pool requires prior snapshot and raw breach candles"
                )
            if self.transition != self.context.observation or not _breached(
                side, lower, upper, self.transition.candle
            ):
                raise AnalysisInputError("terminal transition must be this candle's strict breach")
            if (
                self.previous_candle.reference.series != self.provenance.series
                or self.previous_candle.reference.candle_index + 1
                != self.transition.reference.candle_index
            ):
                raise AnalysisInputError("terminal pool must retain the preceding candle")
            if (
                self.previous_candle.available_at > self.transition.available_at
                or self.previous_candle.reference.closed_at > self.transition.reference.opened_at
                or any(
                    m.provenance.available_at > self.previous_snapshot.available_at
                    for m in self.members
                )
            ):
                raise AnalysisInputError(
                    "terminal transition cannot use later or overlapping evidence"
                )
            if self.previous_candle.reference not in self.provenance.source_candles:
                raise AnalysisInputError("terminal provenance must retain the previous candle")
            failure = _failure(
                side,
                lower,
                upper,
                self.previous_snapshot.available_at,
                self.previous_candle,
                self.transition,
            )
            if self.status == PoolStatus.SWEPT:
                if (
                    failure is not None
                    or self.sweep_reference is None
                    or self.invalidation_reason is not None
                ):
                    raise AnalysisInputError("swept pool requires a confirmed sweep reference")
            elif (
                self.invalidation_reason != failure
                or failure is None
                or self.sweep_reference is not None
            ):
                raise AnalysisInputError("invalidation reason does not match the raw breach facts")

    @property
    def formation_start(self) -> ObservedCandle:
        return self.members[0].pivot

    @property
    def first_confirmed_at(self) -> datetime:
        return self.members[0].provenance.available_at

    @property
    def last_touch(self) -> ObservedCandle:
        return self.members[-1].pivot


@dataclass(frozen=True, slots=True)
class SweepEvent:
    """Confirmed same-candle rejection of an exact pre-breach pool snapshot."""

    pool: LiquidityPool
    previous: ObservedCandle
    breach: ObservedCandle
    context_before: StructureContext
    context: StructureContext
    provenance: EvidenceProvenance
    sweep_id: str = field(init=False)
    side: LiquiditySide = field(init=False)
    extreme_price: Decimal = field(init=False)
    reclaim_close: Decimal = field(init=False)
    confirmed_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.pool, LiquidityPool) or self.pool.status != PoolStatus.ACTIVE:
            raise AnalysisInputError("sweep must retain the active pre-breach pool")
        if not isinstance(self.previous, ObservedCandle) or not isinstance(
            self.breach, ObservedCandle
        ):
            raise AnalysisInputError("sweep requires raw previous and breach candles")
        if not isinstance(self.context_before, StructureContext) or not isinstance(
            self.context, StructureContext
        ):
            raise AnalysisInputError("sweep requires before/after historical context")
        if (
            self.context_before.observation != self.previous
            or self.context.observation != self.breach
        ):
            raise AnalysisInputError("sweep context must match the raw observations")
        if self.previous.reference.candle_index + 1 != self.breach.reference.candle_index:
            raise AnalysisInputError("sweep requires consecutive observed candle indices")
        if (
            self.pool.provenance.series != self.breach.reference.series
            or self.previous.reference.series != self.breach.reference.series
        ):
            raise AnalysisInputError("sweep inputs must belong to one series")
        if self.pool.context.snapshot.candle_index >= self.breach.reference.candle_index:
            raise AnalysisInputError("sweep cannot use a same-candle or future pool")
        if (
            not _breached(
                self.pool.side, self.pool.lower_bound, self.pool.upper_bound, self.breach.candle
            )
            or _failure(
                self.pool.side,
                self.pool.lower_bound,
                self.pool.upper_bound,
                self.pool.provenance.available_at,
                self.previous,
                self.breach,
            )
            is not None
        ):
            raise AnalysisInputError(
                "sweep must strictly breach and reclaim a previously known band"
            )
        required = (
            self.pool.provenance.as_reference(),
            self.context_before.provenance.as_reference(),
            self.context.provenance.as_reference(),
        )
        _metadata(self.provenance, self.breach, required)
        if self.previous.reference not in self.provenance.source_candles:
            raise AnalysisInputError("sweep provenance must retain the preceding candle")
        for name, value in (
            ("sweep_id", self.provenance.evidence_id),
            ("side", self.pool.side),
            (
                "extreme_price",
                self.breach.candle.high
                if self.pool.side == LiquiditySide.BUY_SIDE
                else self.breach.candle.low,
            ),
            ("reclaim_close", self.breach.candle.close),
            ("confirmed_at", self.provenance.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class LiquiditySnapshot:
    """One closed-candle frame; pool updates and sweeps are publication deltas."""

    context: StructureContext
    confirmed_swings: tuple[SwingEvidence, ...]
    pool_updates: tuple[LiquidityPool, ...]
    sweeps: tuple[SweepEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.context, StructureContext):
            raise AnalysisInputError("liquidity snapshot requires structure context")
        for values, expected in (
            (self.confirmed_swings, SwingEvidence),
            (self.pool_updates, LiquidityPool),
            (self.sweeps, SweepEvent),
        ):
            if not isinstance(values, tuple) or not all(
                isinstance(value, expected) for value in values
            ):
                raise AnalysisInputError("snapshot deltas must be immutable typed tuples")
            if any(
                value.provenance.available_at != self.context.provenance.available_at
                or value.provenance.series != self.context.provenance.series
                for value in values
            ):
                raise AnalysisInputError(
                    "snapshot outputs must share the current cutoff and series"
                )
        if any(p.context != self.context for p in self.pool_updates) or any(
            e.context != self.context for e in self.sweeps
        ):
            raise AnalysisInputError("pool and sweep deltas must belong to this exact frame")
        swept = {p.pool_id: p for p in self.pool_updates if p.status == PoolStatus.SWEPT}
        if set(swept) != {e.pool.pool_id for e in self.sweeps} or any(
            swept[e.pool.pool_id].sweep_reference != e.provenance.as_reference()
            or swept[e.pool.pool_id].previous_snapshot != e.pool.provenance.as_reference()
            for e in self.sweeps
        ):
            raise AnalysisInputError("sweep events and terminal pool updates must match")
        if tuple(m.swing for m in self.confirmed_swings) != self.context.snapshot.confirmed_swings:
            raise AnalysisInputError("swing evidence must match this frame's Phase 3 confirmations")
        if len({p.pool_id for p in self.pool_updates}) != len(self.pool_updates) or len(
            {e.pool.pool_id for e in self.sweeps}
        ) != len(self.sweeps):
            raise AnalysisInputError("a pool may be updated or swept only once per candle")
