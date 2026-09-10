"""Immutable single-candle displacement facts and fully sourced ATR references."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.displacement.calculation import (
    difference,
    exact_sum,
    product,
    ratio,
    true_range,
)
from smcsignal.analysis.displacement.config import METHODOLOGY_VERSION, DisplacementConfig
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.models import (
    LiquiditySide,
    LiquiditySnapshot,
    ObservedCandle,
    StructureContext,
    SweepEvent,
    _metadata,
)
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    _text,
)


class SweepContext(StrEnum):
    NONE = "none"
    AFTER_BUY_SIDE = "after_buy_side"
    AFTER_SELL_SIDE = "after_sell_side"
    AFTER_BOTH = "after_both"


@dataclass(frozen=True, slots=True)
class ATRReference:
    """SMA of period true ranges, with period+1 actual observations, no TR0 seed."""

    period: int
    observations: tuple[ObservedCandle, ...]
    context: StructureContext
    provenance: EvidenceProvenance
    true_ranges: tuple[Decimal, ...] = field(init=False)
    total_true_range: Decimal = field(init=False)
    value: Decimal = field(init=False)
    start_index: int = field(init=False)
    end_index: int = field(init=False)
    reference_candle: CandleReference = field(init=False)

    def __post_init__(self) -> None:
        if type(self.period) is not int or not 1 <= self.period <= 1000:
            raise AnalysisInputError("ATR period must be an integer from 1 to 1000")
        if (
            not isinstance(self.observations, tuple)
            or len(self.observations) != self.period + 1
            or not all(isinstance(c, ObservedCandle) for c in self.observations)
        ):
            raise AnalysisInputError(
                "ATR requires an immutable complete period+1 observation window"
            )
        if (
            not isinstance(self.context, StructureContext)
            or self.context.observation != self.observations[-1]
        ):
            raise AnalysisInputError("ATR context must be its last reference candle")
        for previous, current in zip(self.observations, self.observations[1:], strict=False):
            if (
                previous.reference.series != current.reference.series
                or previous.reference.candle_index + 1 != current.reference.candle_index
                or previous.reference.closed_at > current.reference.opened_at
                or previous.available_at > current.available_at
            ):
                raise AnalysisInputError(
                    "ATR source observations must be consecutive, chronological, and available"
                )
        _metadata(self.provenance, self.observations[-1], (self.context.provenance.as_reference(),))
        if (
            self.provenance.source_candles != tuple(c.reference for c in self.observations)
            or self.provenance.input_prefix_hash != self.context.provenance.input_prefix_hash
        ):
            raise AnalysisInputError(
                "ATR provenance must retain its exact source window and prefix"
            )
        values = tuple(
            true_range(current.candle.high, current.candle.low, previous.candle.close)
            for previous, current in zip(self.observations, self.observations[1:], strict=False)
        )
        total = exact_sum(values)
        for name, value in (
            ("true_ranges", values),
            ("total_true_range", total),
            ("value", ratio(total, Decimal(self.period))),
            ("start_index", self.observations[1].reference.candle_index),
            ("end_index", self.observations[-1].reference.candle_index),
            ("reference_candle", self.observations[-1].reference),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class DisplacementMetrics:
    observation: ObservedCandle
    atr_reference: ATRReference | None
    body_size: Decimal = field(init=False)
    range_size: Decimal = field(init=False)
    close_location_ratio: Decimal | None = field(init=False)
    body_atr_ratio: Decimal | None = field(init=False)
    range_atr_ratio: Decimal | None = field(init=False)
    direction: TrendDirection | None = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.observation, ObservedCandle):
            raise AnalysisInputError("metrics require a closed ObservedCandle")
        reference = self.atr_reference
        if reference is not None:
            if not isinstance(reference, ATRReference) or (
                reference.end_index + 1 != self.observation.reference.candle_index
                or reference.provenance.series != self.observation.reference.series
                or reference.reference_candle.closed_at > self.observation.reference.opened_at
                or reference.provenance.available_at > self.observation.available_at
            ):
                raise AnalysisInputError(
                    "displacement ATR must be the available reference ending at t-1"
                )
        candle = self.observation.candle
        body, span = (
            difference(candle.close, candle.open).copy_abs(),
            difference(candle.high, candle.low),
        )
        direction = (
            None
            if candle.close == candle.open
            else (TrendDirection.BULLISH if candle.close > candle.open else TrendDirection.BEARISH)
        )
        body_relative = range_relative = None
        if reference is not None and reference.total_true_range > 0:
            body_relative = ratio(
                product(body, Decimal(reference.period)), reference.total_true_range
            )
            range_relative = ratio(
                product(span, Decimal(reference.period)), reference.total_true_range
            )
        for name, value in (
            ("body_size", body),
            ("range_size", span),
            ("direction", direction),
            (
                "close_location_ratio",
                ratio(difference(candle.close, candle.low), span) if span else None,
            ),
            ("body_atr_ratio", body_relative),
            ("range_atr_ratio", range_relative),
        ):
            object.__setattr__(self, name, value)


def qualifies(metrics: DisplacementMetrics, config: DisplacementConfig) -> bool:
    """Inclusive exact cross-products; display ratios never decide acceptance."""
    reference = metrics.atr_reference
    if reference is None or metrics.direction is None or metrics.range_size == 0:
        return False
    if reference.period != config.atr_period:
        raise AnalysisInputError("ATR period differs from displacement configuration")
    n, total = Decimal(reference.period), reference.total_true_range
    if total <= product(config.atr_floor, n):
        return False
    if product(metrics.body_size, n) < product(config.min_body_atr, total) or product(
        metrics.range_size, n
    ) < product(config.min_range_atr, total):
        return False
    close_from_low = difference(metrics.observation.candle.close, metrics.observation.candle.low)
    if metrics.direction == TrendDirection.BULLISH:
        return close_from_low >= product(config.bullish_close_min, metrics.range_size)
    return close_from_low <= product(config.bearish_close_max, metrics.range_size)


def validate_sweeps(
    sweeps: tuple[SweepEvent, ...], observation: ObservedCandle, lookback: int
) -> None:
    if not isinstance(sweeps, tuple) or not all(isinstance(s, SweepEvent) for s in sweeps):
        raise AnalysisInputError("preceding sweeps must be an immutable SweepEvent tuple")
    if (
        len({s.sweep_id for s in sweeps}) != len(sweeps)
        or len({s.breach.reference.candle_index for s in sweeps}) > 1
    ):
        raise AnalysisInputError("preceding sweeps must be one unique prior-candle cohort")
    for sweep in sweeps:
        if (
            sweep.provenance.series != observation.reference.series
            or not 1
            <= observation.reference.candle_index - sweep.breach.reference.candle_index
            <= lookback
            or sweep.confirmed_at > observation.reference.opened_at
        ):
            raise AnalysisInputError(
                "preceding sweep must be in-window and known before this candle opened"
            )


@dataclass(frozen=True, slots=True)
class DisplacementEvent:
    settings: DisplacementConfig
    price_unit: str
    metrics: DisplacementMetrics
    context: StructureContext
    preceding_sweeps: tuple[SweepEvent, ...]
    provenance: EvidenceProvenance
    event_id: str = field(init=False)
    symbol: str = field(init=False)
    timeframe: str = field(init=False)
    direction: TrendDirection = field(init=False)
    start_index: int = field(init=False)
    end_index: int = field(init=False)
    detection_index: int = field(init=False)
    timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)
    threshold_version: str = field(init=False, default=METHODOLOGY_VERSION)
    preceding_sweep_references: tuple[EvidenceReference, ...] = field(init=False)
    sweep_context: SweepContext = field(init=False)

    def __post_init__(self) -> None:
        _text(self.price_unit, "price_unit")
        if not isinstance(self.settings, DisplacementConfig) or not isinstance(
            self.metrics, DisplacementMetrics
        ):
            raise AnalysisInputError("displacement requires typed configuration and metrics")
        if (
            not isinstance(self.context, StructureContext)
            or self.context.observation != self.metrics.observation
        ):
            raise AnalysisInputError("displacement context must match the measured observation")
        if not qualifies(self.metrics, self.settings):
            raise AnalysisInputError(
                "displacement event does not satisfy the exact configured criteria"
            )
        observation = self.metrics.observation
        reference = self.metrics.atr_reference
        assert reference is not None and self.metrics.direction is not None
        validate_sweeps(self.preceding_sweeps, observation, self.settings.sweep_lookback_bars)
        if any(s.pool.settings.price_unit != self.price_unit for s in self.preceding_sweeps):
            raise AnalysisInputError("sweep and displacement price units differ")
        sweep_refs = tuple(s.provenance.as_reference() for s in self.preceding_sweeps)
        _metadata(
            self.provenance,
            observation,
            (
                self.context.provenance.as_reference(),
                reference.provenance.as_reference(),
                *sweep_refs,
            ),
        )
        if self.provenance.input_prefix_hash != self.context.provenance.input_prefix_hash:
            raise AnalysisInputError("displacement must use the current observed prefix hash")
        sides = {s.side for s in self.preceding_sweeps}
        relation = SweepContext.NONE
        if len(sides) == 2:
            relation = SweepContext.AFTER_BOTH
        elif sides:
            relation = (
                SweepContext.AFTER_BUY_SIDE
                if LiquiditySide.BUY_SIDE in sides
                else SweepContext.AFTER_SELL_SIDE
            )
        for name, value in (
            ("event_id", self.provenance.evidence_id),
            ("symbol", self.provenance.series.symbol),
            ("timeframe", self.provenance.series.timeframe),
            ("direction", self.metrics.direction),
            ("start_index", observation.reference.candle_index),
            ("end_index", observation.reference.candle_index),
            ("detection_index", observation.reference.candle_index),
            ("timestamp", observation.reference.opened_at),
            ("available_at", observation.available_at),
            ("preceding_sweep_references", sweep_refs),
            ("sweep_context", relation),
        ):
            object.__setattr__(self, name, value)

    @property
    def atr_reference(self) -> ATRReference:
        assert self.metrics.atr_reference is not None
        return self.metrics.atr_reference

    @property
    def body_size(self) -> Decimal:
        return self.metrics.body_size

    @property
    def range_size(self) -> Decimal:
        return self.metrics.range_size

    @property
    def body_atr_ratio(self) -> Decimal:
        assert self.metrics.body_atr_ratio is not None
        return self.metrics.body_atr_ratio

    @property
    def range_atr_ratio(self) -> Decimal:
        assert self.metrics.range_atr_ratio is not None
        return self.metrics.range_atr_ratio

    @property
    def close_location_ratio(self) -> Decimal:
        assert self.metrics.close_location_ratio is not None
        return self.metrics.close_location_ratio

    @property
    def observation(self) -> ObservedCandle:
        return self.metrics.observation


@dataclass(frozen=True, slots=True)
class DisplacementSnapshot:
    """One immutable assessment per upstream closed candle; events are deltas."""

    liquidity: LiquiditySnapshot
    settings: DisplacementConfig
    price_unit: str
    metrics: DisplacementMetrics
    atr_reference: ATRReference | None
    atr_current: ATRReference | None
    preceding_sweeps: tuple[SweepEvent, ...]
    events: tuple[DisplacementEvent, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if (
            not isinstance(self.liquidity, LiquiditySnapshot)
            or not isinstance(self.settings, DisplacementConfig)
            or not isinstance(self.metrics, DisplacementMetrics)
        ):
            raise AnalysisInputError(
                "displacement frame requires typed upstream data, metrics, and settings"
            )
        _text(self.price_unit, "price_unit")
        observation = self.liquidity.context.observation
        index = observation.reference.candle_index
        if (
            self.metrics.observation != observation
            or self.metrics.atr_reference != self.atr_reference
        ):
            raise AnalysisInputError("frame metrics must use this observation and its prior ATR")
        if (self.atr_reference is None) != (index <= self.settings.atr_period):
            raise AnalysisInputError(
                "prior ATR readiness is inconsistent with the fixed-origin history"
            )
        if (self.atr_current is None) != (index < self.settings.atr_period):
            raise AnalysisInputError(
                "current ATR readiness is inconsistent with the fixed-origin history"
            )
        dependencies = [self.liquidity.context.provenance.as_reference()]
        for reference in (self.atr_reference, self.atr_current):
            if reference is not None:
                if (
                    not isinstance(reference, ATRReference)
                    or reference.period != self.settings.atr_period
                ):
                    raise AnalysisInputError("ATR reference does not match this configuration")
                dependencies.append(reference.provenance.as_reference())
        if self.atr_current is not None and self.atr_current.context != self.liquidity.context:
            raise AnalysisInputError(
                "current ATR must end at this frame, for use on the next candle"
            )
        validate_sweeps(self.preceding_sweeps, observation, self.settings.sweep_lookback_bars)
        dependencies.extend(s.provenance.as_reference() for s in self.preceding_sweeps)
        if (
            not isinstance(self.events, tuple)
            or len(self.events) > 1
            or not all(isinstance(e, DisplacementEvent) for e in self.events)
        ):
            raise AnalysisInputError(
                "single-candle events must be an immutable tuple of zero or one event"
            )
        if bool(self.events) != qualifies(self.metrics, self.settings):
            raise AnalysisInputError("frame events must exactly match the objective criteria")
        for event in self.events:
            if (
                event.metrics != self.metrics
                or event.settings != self.settings
                or event.context != self.liquidity.context
                or event.preceding_sweeps != self.preceding_sweeps
                or event.price_unit != self.price_unit
            ):
                raise AnalysisInputError("event facts must belong to this exact frame")
            dependencies.append(event.provenance.as_reference())
        _metadata(self.provenance, observation, tuple(dependencies))
        if self.provenance.input_prefix_hash != self.liquidity.context.provenance.input_prefix_hash:
            raise AnalysisInputError(
                "displacement frame must retain the upstream input prefix hash"
            )
