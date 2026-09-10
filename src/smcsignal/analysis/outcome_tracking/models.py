"""Immutable BUY_SIGNAL outcome records and analytics. Not orders, entries, or exits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    _instant,
)
from smcsignal.analysis.signal_engine.models import (
    SignalSnapshot,
    SignalStatus,
)
from smcsignal.analysis.signal_engine.models import (
    current_observation as signal_observation,
)


class OutcomeStatus(StrEnum):
    OPEN = "OPEN"
    WIN = "WIN"
    LOSS = "LOSS"
    FLAT = "FLAT"


def current_observation(frame: SignalSnapshot) -> ObservedCandle:
    """Delegate to the existing Phase 17 observation; no second price feed exists."""

    return signal_observation(frame.upstream)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _price(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise AnalysisInputError(f"{name} must be a positive, finite Decimal price")


def _index(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise AnalysisInputError(f"{name} must be a nonnegative integer candle index")


@dataclass(frozen=True, slots=True)
class SignalOutcome:
    """One immutable lifecycle version of one BUY_SIGNAL outcome.

    Records are per-version facts: extremes are partial while OPEN, finals exist
    only on the finalizing version, and published versions never change. This is
    an analytics record over published spot signals, never an order or fill.
    """

    settings: OutcomeTrackingConfig
    status: OutcomeStatus
    outcome_id: str
    signal_id: str
    setup_identity: str
    symbol: str
    timeframe: str
    horizon_bars: int
    reference: CandleReference
    reference_close: Decimal
    candles_observed: int
    mfe_price: Decimal | None
    mfe_index: int | None
    mae_price: Decimal | None
    mae_index: int | None
    final_index: int | None
    final_close: Decimal | None
    created_available_at: datetime
    evaluated_available_at: datetime | None
    finalized_available_at: datetime | None
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, OutcomeTrackingConfig):
            raise AnalysisInputError("outcome requires OutcomeTrackingConfig")
        if not isinstance(self.status, OutcomeStatus):
            raise AnalysisInputError("status must be OPEN, WIN, LOSS, or FLAT")
        for name, value in (
            ("outcome_id", self.outcome_id),
            ("signal_id", self.signal_id),
            ("setup_identity", self.setup_identity),
            ("symbol", self.symbol),
            ("timeframe", self.timeframe),
        ):
            _text(value, name)
        if type(self.horizon_bars) is not int or self.horizon_bars != self.settings.horizon_bars:
            raise AnalysisInputError("outcome horizon must equal the configuration horizon")
        if not isinstance(self.reference, CandleReference):
            raise AnalysisInputError("outcome requires the signal CandleReference")
        _price(self.reference_close, "reference_close")
        _index(self.candles_observed, "candles_observed")
        if self.candles_observed > self.horizon_bars:
            raise AnalysisInputError("candles_observed cannot exceed the horizon")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("outcome requires provenance")
        if self.provenance.producer != "outcome-record":
            raise AnalysisInputError("outcome producer must be outcome-record")
        object.__setattr__(
            self,
            "created_available_at",
            _instant(self.created_available_at, "created_available_at"),
        )
        if self.evaluated_available_at is not None:
            object.__setattr__(
                self,
                "evaluated_available_at",
                _instant(self.evaluated_available_at, "evaluated_available_at"),
            )
        if self.finalized_available_at is not None:
            object.__setattr__(
                self,
                "finalized_available_at",
                _instant(self.finalized_available_at, "finalized_available_at"),
            )
        if (
            self.evaluated_available_at is not None
            and self.evaluated_available_at < self.created_available_at
        ):
            raise AnalysisInputError("evaluation cannot precede outcome creation")
        if self.finalized_available_at is not None and (
            self.evaluated_available_at is None
            or self.finalized_available_at < self.evaluated_available_at
        ):
            raise AnalysisInputError(
                "finalization requires a later or equal evaluation availability"
            )
        extremes: tuple[tuple[Decimal | None, int | None, str], ...] = (
            (self.mfe_price, self.mfe_index, "mfe"),
            (self.mae_price, self.mae_index, "mae"),
        )
        for price, index, name in extremes:
            if (price is None) != (index is None):
                raise AnalysisInputError(f"{name} price and index must be present together")
            if price is None or index is None:
                continue
            _price(price, f"{name}_price")
            _index(index, f"{name}_index")
            if index <= self.reference.candle_index:
                raise AnalysisInputError(f"{name} index must follow the signal candle")
            if index > self.reference.candle_index + self.candles_observed:
                raise AnalysisInputError(f"{name} index must lie inside the observed window")
        if (self.candles_observed == 0) != (self.mfe_price is None):
            raise AnalysisInputError("extremes exist exactly once evaluation candles are observed")
        if (self.candles_observed == 0) != (self.evaluated_available_at is None):
            raise AnalysisInputError(
                "evaluation availability exists exactly once candles are observed"
            )
        if (self.final_index is None) != (self.final_close is None):
            raise AnalysisInputError("final index and close must be present together")
        if self.status is OutcomeStatus.OPEN:
            if self.final_index is not None or self.finalized_available_at is not None:
                raise AnalysisInputError("an open outcome cannot carry finals")
            if self.candles_observed >= self.horizon_bars:
                raise AnalysisInputError("an open outcome must still await evaluation candles")
        else:
            if self.candles_observed != self.horizon_bars:
                raise AnalysisInputError("a final outcome is classified exactly at the horizon")
            final_index = self.final_index
            final_close = self.final_close
            if final_index is None or final_close is None:
                raise AnalysisInputError("a final outcome carries final index and close")
            _index(final_index, "final_index")
            if final_index != self.reference.candle_index + self.candles_observed:
                raise AnalysisInputError(
                    "the final candle is the last evaluation candle of the horizon"
                )
            _price(final_close, "final_close")
            if self.finalized_available_at is None:
                raise AnalysisInputError("a final outcome requires finalization availability")
            difference = final_close - self.reference_close
            expected = (
                OutcomeStatus.WIN
                if difference > 0
                else OutcomeStatus.LOSS
                if difference < 0
                else OutcomeStatus.FLAT
            )
            if self.status is not expected:
                raise AnalysisInputError("status must match the exact sign of the final difference")
        if self.provenance.series != self.reference.series:
            raise AnalysisInputError("outcome provenance must use the signal series")
        if self.reference not in self.provenance.source_candles:
            raise AnalysisInputError("outcome provenance must include the signal candle")
        publication = (
            self.evaluated_available_at
            if self.evaluated_available_at is not None
            else self.created_available_at
        )
        if self.provenance.available_at != publication:
            raise AnalysisInputError("outcome provenance is published at the version cutoff")


@dataclass(frozen=True, slots=True)
class AnalyticsSummary:
    """Deterministic aggregate statistics over finalized outcomes only.

    Counts are exact integers, return sums are exact Decimal additions, and
    rates/averages are descriptive 50-significant-digit ratios. These are
    software statistics of published records, not a backtest, performance claim,
    or expected-return forecast. Open outcomes contribute only to open_count.
    """

    total_buy_signals: int
    open_count: int
    win_count: int
    loss_count: int
    flat_count: int
    finalized_count: int
    final_return_sum: Decimal
    mfe_return_sum: Decimal
    mae_return_sum: Decimal
    win_rate: Decimal | None
    average_final_return: Decimal | None
    average_mfe_return: Decimal | None
    average_mae_return: Decimal | None

    def __post_init__(self) -> None:
        counts = (
            self.total_buy_signals,
            self.open_count,
            self.win_count,
            self.loss_count,
            self.flat_count,
            self.finalized_count,
        )
        if any(type(count) is not int or count < 0 for count in counts):
            raise AnalysisInputError("analytics counts must be nonnegative integers")
        if self.win_count + self.loss_count + self.flat_count != self.finalized_count:
            raise AnalysisInputError("win, loss, and flat counts must sum to finalized_count")
        if self.finalized_count + self.open_count != self.total_buy_signals:
            raise AnalysisInputError("finalized and open counts must sum to total_buy_signals")
        sums = (self.final_return_sum, self.mfe_return_sum, self.mae_return_sum)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in sums):
            raise AnalysisInputError("analytics sums must be finite Decimals")
        rates = (
            self.win_rate,
            self.average_final_return,
            self.average_mfe_return,
            self.average_mae_return,
        )
        if self.finalized_count == 0:
            if any(value is not None for value in rates):
                raise AnalysisInputError(
                    "rates and averages are undefined without finalized outcomes"
                )
        else:
            for value in rates:
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise AnalysisInputError("rates and averages must be finite Decimals")
            win_rate = self.win_rate
            if not isinstance(win_rate, Decimal):
                raise AnalysisInputError("win_rate must be a Decimal when outcomes are finalized")
            if not Decimal(0) <= win_rate <= Decimal(1):
                raise AnalysisInputError("win_rate must lie between zero and one")


@dataclass(frozen=True, slots=True)
class OutcomeSnapshot:
    """Per-frame outcome deltas and the current analytics view.

    The original Phase 17 SignalSnapshot instance is retained unchanged. Open
    outcomes are never flushed at end-of-series; no such API exists.
    """

    settings: OutcomeTrackingConfig
    upstream: SignalSnapshot
    created: tuple[SignalOutcome, ...]
    evaluated: tuple[SignalOutcome, ...]
    completed: tuple[SignalOutcome, ...]
    analytics: AnalyticsSummary
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, OutcomeTrackingConfig) or not isinstance(
            self.upstream, SignalSnapshot
        ):
            raise AnalysisInputError(
                "outcome snapshot requires settings and an existing SignalSnapshot"
            )
        observation = current_observation(self.upstream)
        for name, records in (
            ("created", self.created),
            ("evaluated", self.evaluated),
            ("completed", self.completed),
        ):
            if not isinstance(records, tuple) or not all(
                isinstance(record, SignalOutcome) for record in records
            ):
                raise AnalysisInputError(f"{name} must be a tuple of SignalOutcome records")
            if len({record.outcome_id for record in records}) != len(records):
                raise AnalysisInputError(f"duplicate outcome versions in {name} are forbidden")
            for record in records:
                if record.settings != self.settings:
                    raise AnalysisInputError(f"{name} records must use this exact configuration")
                if record.symbol != observation.reference.series.symbol:
                    raise AnalysisInputError(f"{name} records must match the upstream symbol")
                if record.timeframe != observation.reference.series.timeframe:
                    raise AnalysisInputError(f"{name} records must match the upstream timeframe")
        if len(self.created) > 1:
            raise AnalysisInputError("at most one outcome is created per frame")
        if bool(self.created) is not (self.upstream.status is SignalStatus.BUY_SIGNAL):
            raise AnalysisInputError("outcomes are created exactly on BUY_SIGNAL frames")
        for record in self.created:
            if record.status is not OutcomeStatus.OPEN or record.candles_observed != 0:
                raise AnalysisInputError("a created outcome is the initial open version")
            if record.signal_id != self.upstream.signal_id:
                raise AnalysisInputError("a created outcome must track the upstream signal")
        for record in self.evaluated:
            if record.candles_observed < 1:
                raise AnalysisInputError("an evaluated version observed at least one candle")
        for record in self.completed:
            if record.status is OutcomeStatus.OPEN:
                raise AnalysisInputError("a completed version is not open")
            if record.outcome_id not in {item.outcome_id for item in self.evaluated}:
                raise AnalysisInputError("a completed version must also be evaluated this frame")
        if not isinstance(self.analytics, AnalyticsSummary):
            raise AnalysisInputError("analytics must be an AnalyticsSummary")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("outcome snapshot requires provenance")
        if self.provenance.producer != "outcome-frame":
            raise AnalysisInputError("snapshot producer must be outcome-frame")
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("outcome snapshot must reuse the current consumed prefix")
        if self.provenance.series != self.upstream.provenance.series:
            raise AnalysisInputError("outcome snapshot series must be the upstream series")
        records = (*self.created, *self.evaluated)
        dependencies: tuple[EvidenceReference, ...] = (
            self.upstream.provenance.as_reference(),
            *(record.provenance.as_reference() for record in records),
        )
        _metadata(self.provenance, observation, dependencies)
