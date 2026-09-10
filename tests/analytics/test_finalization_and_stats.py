"""Phase 26A: real market-evaluated finals feed StrategyStats and monthly reports.

Only the existing Phase 18 market outcome evaluator (later candles of the
signal's own series) can transition an observed outcome from OPEN. A delivered
message, a clock, or a configuration knob never can. Outcomes whose horizon
was never observed stay OPEN forever — the documented limitation of the
current architecture, pinned here as behavior.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import (
    OutcomeStatus,
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
)
from smcsignal.analytics import BREAKEVEN, AnalyticsObserver
from tests.analytics.helpers import buy_frames, candles_for, observed_engine, publish
from tests.outcome_tracking.helpers import FALLING_TAIL, FLAT_TAIL


def _observe_and_finalize(candles=None, *, horizon=None, threshold=10):
    config = (
        OutcomeTrackingConfig() if horizon is None else OutcomeTrackingConfig(horizon_bars=horizon)
    )
    observer = AnalyticsObserver(config)
    adapter, eligible = observed_engine(observer, candles, threshold=threshold)
    frames = publish(adapter, eligible)
    tracker = OutcomeTrackingAnalyzer(config)
    finals: list = []
    for frame in frames:
        finals.extend(tracker.update(frame).completed)
    recorded = [observer.record_finalized(record) for record in finals]
    return observer, frames, tuple(finals), recorded


def test_market_evaluator_win_feeds_strategy_stats() -> None:
    observer, frames, finals, recorded = _observe_and_finalize()
    assert recorded == [True]
    assert finals[0].status is OutcomeStatus.WIN

    stats = observer.strategy_stats
    buys = len(buy_frames(frames))
    assert stats.total_buy_signals == buys
    assert stats.finalized_count == 1
    assert stats.win_count == 1
    assert stats.loss_count == 0
    assert stats.flat_count == 0
    assert stats.open_count == buys - 1
    assert stats.win_rate == Decimal(1)
    assert observer.finalized_outcomes == finals
    assert len(observer.open_outcomes) == buys - 1


def test_loss_and_breakeven_finals_are_market_facts() -> None:
    observer, _, finals, _ = _observe_and_finalize(candles_for(FALLING_TAIL))
    assert finals and finals[0].status is OutcomeStatus.LOSS
    assert observer.strategy_stats.loss_count == 1

    observer, _, finals, _ = _observe_and_finalize(candles_for(FLAT_TAIL))
    assert finals and finals[0].status is OutcomeStatus.FLAT
    assert finals[0].status is BREAKEVEN  # vocabulary alias, not a new status
    assert observer.strategy_stats.flat_count == 1
    assert observer.strategy_stats.win_count == 0
    assert observer.strategy_stats.win_rate == Decimal(0)


def test_mixed_finals_match_the_phase18_evaluator_exactly() -> None:
    observer, _, finals, _ = _observe_and_finalize(candles_for(FLAT_TAIL), horizon=3)
    wins = sum(record.status is OutcomeStatus.WIN for record in finals)
    losses = sum(record.status is OutcomeStatus.LOSS for record in finals)
    assert (wins, losses) == (3, 2)  # hand-audited Phase 18 expectation

    stats = observer.strategy_stats
    assert stats.finalized_count == len(finals) == 5
    assert stats.win_count == wins
    assert stats.loss_count == losses
    assert stats.flat_count == 0
    assert stats.win_rate == Decimal(wins) / Decimal(len(finals))


def test_monthly_report_carries_finalized_outcomes() -> None:
    observer, frames, finals, _ = _observe_and_finalize(candles_for(FLAT_TAIL), horizon=3)
    report = observer.monthly_report()

    assert [month.month for month in report.months] == ["2024-01"]
    month = report.months[0]
    assert month.finalized_count == 5
    assert month.win_count == 3 and month.loss_count == 2
    assert month.summary.open_count == len(buy_frames(frames)) - 5
    assert report.totals == observer.strategy_stats
    assert report.totals.finalized_count == month.finalized_count


def test_observed_outcome_stays_open_without_market_evaluation() -> None:
    """Documented limitation: no live feed exists after publication.

    Without horizon candles evaluated by the real Phase 18 evaluator, every
    observed outcome remains OPEN and contributes to no WIN/LOSS statistic.
    """

    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    assert frames

    stats = observer.strategy_stats
    assert stats.total_buy_signals == len(buy_frames(frames))
    assert stats.open_count == stats.total_buy_signals
    assert stats.finalized_count == 0
    assert stats.win_rate is None
    report = observer.monthly_report()
    assert report.totals.finalized_count == 0
    assert all(month.finalized_count == 0 for month in report.months)


def test_horizon_never_observed_keeps_outcomes_open() -> None:
    config = OutcomeTrackingConfig(horizon_bars=13)  # longer than the fixture tail
    observer = AnalyticsObserver(config)
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    tracker = OutcomeTrackingAnalyzer(config)
    for frame in frames:
        assert tracker.update(frame).completed == ()
    assert observer.strategy_stats.finalized_count == 0
    assert observer.strategy_stats.open_count == len(buy_frames(frames))


def test_open_record_cannot_be_recorded_as_finalized() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    publish(adapter, eligible)
    open_record = observer.open_outcomes[0]
    with pytest.raises(AnalysisInputError, match="market outcome"):
        observer.record_finalized(open_record)
    assert observer.strategy_stats.finalized_count == 0
