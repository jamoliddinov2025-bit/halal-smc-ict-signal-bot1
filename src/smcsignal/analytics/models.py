"""Immutable Phase 26A analytics records over published spot signals.

These records are strictly downstream facts about signals the real Phase 17
engine already published. They never generate, gate, veto, reinterpret, or
modify a signal, a halal classification, an eligibility decision, a Phase 23
governance decision, or a delivery. Every record is frozen, every count is an
exact integer, and every rate is a descriptive Phase 18 ratio.

``DeliveryState`` is a transport fact and has no representation here: nothing
in this module imports ``smcsignal.delivery``, and no transport state can ever
be read as a trade outcome. Only the existing Phase 18 market-outcome
evaluator (later candles of the signal's own series) can finalize an outcome.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import (
    AnalyticsSummary,
    OutcomeStatus,
    SignalOutcome,
    aggregate,
)

#: Breakeven vocabulary for the frozen Phase 18 status set: an outcome whose
#: final close equals its reference close is classified ``FLAT`` by the exact
#: sign rule. This alias introduces no new status; Phase 18 stays unchanged.
BREAKEVEN: OutcomeStatus = OutcomeStatus.FLAT


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def _valid_month(month: str) -> bool:
    """Strict UTC 'YYYY-MM' shape without ambient libraries."""

    year, separator, rest = month[:4], month[4:5], month[5:]
    if separator != "-" or len(rest) != 2 or not (year.isdigit() and rest.isdigit()):
        return False
    return 1 <= int(rest) <= 12


@dataclass(frozen=True, slots=True)
class SignalObservation:
    """One publication observation: a BUY_SIGNAL entered analytics as OPEN.

    The observation carries exactly the initial open Phase 18 outcome version
    (``candles_observed == 0``, no extremes, no finals). It is a record about a
    published signal, never an order, an entry, or a delivery fact.
    """

    outcome: SignalOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, SignalOutcome):
            raise AnalysisInputError("observation requires a Phase 18 SignalOutcome")
        if self.outcome.status is not OutcomeStatus.OPEN:
            raise AnalysisInputError("an observed outcome is the initial open version")
        if self.outcome.candles_observed != 0:
            raise AnalysisInputError("an observed outcome has not consumed evaluation candles")

    @property
    def outcome_id(self) -> str:
        return self.outcome.outcome_id

    @property
    def signal_id(self) -> str:
        return self.outcome.signal_id


@dataclass(frozen=True, slots=True)
class StrategyStats:
    """Descriptive strategy statistics over observed published BUY signals.

    A validated view over the Phase 18 ``AnalyticsSummary`` computed by the
    frozen ``aggregate()`` arithmetic: counts are exact integers, sums are exact
    Decimal additions of Phase 18 descriptive ratios, and rates/averages are
    undefined (None) while no outcome is finalized. Open outcomes contribute
    only to ``open_count``. This is a software statistic of published records,
    not a backtest result, a performance claim, or an expected-return forecast.
    """

    summary: AnalyticsSummary

    def __post_init__(self) -> None:
        if not isinstance(self.summary, AnalyticsSummary):
            raise AnalysisInputError("strategy stats require a Phase 18 AnalyticsSummary")

    @property
    def total_buy_signals(self) -> int:
        return self.summary.total_buy_signals

    @property
    def open_count(self) -> int:
        return self.summary.open_count

    @property
    def finalized_count(self) -> int:
        return self.summary.finalized_count

    @property
    def win_count(self) -> int:
        return self.summary.win_count

    @property
    def loss_count(self) -> int:
        return self.summary.loss_count

    @property
    def flat_count(self) -> int:
        """Breakeven count (Phase 18 ``FLAT``): final close equals reference."""

        return self.summary.flat_count

    @property
    def win_rate(self) -> Decimal | None:
        return self.summary.win_rate

    @property
    def average_final_return(self) -> Decimal | None:
        return self.summary.average_final_return

    @property
    def average_mfe_return(self) -> Decimal | None:
        return self.summary.average_mfe_return

    @property
    def average_mae_return(self) -> Decimal | None:
        return self.summary.average_mae_return


def strategy_stats(finalized: tuple[SignalOutcome, ...], *, open_count: int) -> StrategyStats:
    """Deterministic strategy statistics via the frozen Phase 18 aggregation.

    ``finalized`` must be finalized outcomes in observation order; ``open_count``
    counts observed signals still awaiting evaluation candles. No arithmetic is
    re-implemented here: the Phase 18 ``aggregate()`` is the single source.
    """

    if not isinstance(finalized, tuple) or not all(
        isinstance(record, SignalOutcome) for record in finalized
    ):
        raise AnalysisInputError("finalized must be a tuple of SignalOutcome records")
    return StrategyStats(
        aggregate(finalized, total_buy_signals=open_count + len(finalized), open_count=open_count)
    )


@dataclass(frozen=True, slots=True)
class MonthlySummary:
    """One calendar month (UTC) of descriptive statistics over observed signals.

    The month key is the signal candle's ``opened_at`` month, so a summary is a
    fact about when signals were published, never about when messages were
    delivered. The summary reuses the Phase 18 ``AnalyticsSummary`` semantics:
    open signals of the month contribute only to ``open_count``.
    """

    month: str
    summary: AnalyticsSummary

    def __post_init__(self) -> None:
        _text(self.month, "month")
        if not _valid_month(self.month):
            raise AnalysisInputError("month must be a UTC 'YYYY-MM' key")
        if not isinstance(self.summary, AnalyticsSummary):
            raise AnalysisInputError("monthly summary requires a Phase 18 AnalyticsSummary")

    @property
    def finalized_count(self) -> int:
        return self.summary.finalized_count

    @property
    def win_count(self) -> int:
        return self.summary.win_count

    @property
    def loss_count(self) -> int:
        return self.summary.loss_count

    @property
    def flat_count(self) -> int:
        return self.summary.flat_count


@dataclass(frozen=True, slots=True)
class MonthlyReport:
    """Deterministic chronological report over observed published signals.

    Months appear in ascending key order, each computed by the frozen Phase 18
    aggregation; ``totals`` covers exactly the same observations, so month
    counts and totals must agree. The report describes published-signal
    outcomes only; delivery state never participates.
    """

    months: tuple[MonthlySummary, ...]
    totals: StrategyStats

    def __post_init__(self) -> None:
        if not isinstance(self.months, tuple) or not all(
            isinstance(month, MonthlySummary) for month in self.months
        ):
            raise AnalysisInputError("months must be a tuple of MonthlySummary records")
        keys = [month.month for month in self.months]
        if len(set(keys)) != len(keys) or keys != sorted(keys):
            raise AnalysisInputError("months must be unique and in ascending order")
        if not isinstance(self.totals, StrategyStats):
            raise AnalysisInputError("report totals must be StrategyStats")
        checks: tuple[tuple[str, int, Callable[[AnalyticsSummary], int]], ...] = (
            ("total_buy_signals", self.totals.total_buy_signals, lambda s: s.total_buy_signals),
            ("open_count", self.totals.open_count, lambda s: s.open_count),
            ("finalized_count", self.totals.finalized_count, lambda s: s.finalized_count),
            ("win_count", self.totals.win_count, lambda s: s.win_count),
            ("loss_count", self.totals.loss_count, lambda s: s.loss_count),
            ("flat_count", self.totals.flat_count, lambda s: s.flat_count),
        )
        for name, total, gather in checks:
            summed = sum(gather(month.summary) for month in self.months)
            if summed != total:
                raise AnalysisInputError(f"monthly {name} must sum to the report totals")
