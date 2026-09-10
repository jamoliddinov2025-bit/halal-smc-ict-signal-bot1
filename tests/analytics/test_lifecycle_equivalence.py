"""CRITICAL Phase 26B equivalence: lifecycle ≡ the Phase 26A manual bridge.

For identical real frame sequences, path A (the Phase 26A hand-rolled observer
+ separate Phase 18 evaluator bridge) and path B (``SignalOutcomeLifecycle``)
must produce identical OPEN observations, identical finalized outcomes,
identical StrategyStats, and identical MonthlyReports — across WIN, LOSS,
BREAKEVEN, mixed, and open-only scenarios. Any divergence would mean the
composition invented, dropped, or reordered a fact.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.outcome_tracking import OutcomeTrackingAnalyzer, OutcomeTrackingConfig
from smcsignal.analytics import AnalyticsObserver, SignalOutcomeLifecycle, run_lifecycle
from tests.analytics.helpers import eligibility_chain, manual_bridge, publish, real_engine
from tests.outcome_tracking.helpers import FALLING_TAIL, FLAT_TAIL, candles_for

# (candles, horizon) scenarios: WIN-only, LOSS-only, BREAKEVEN-only, mixed,
# and an open-only history whose horizon is never observed.
SCENARIOS = (
    (None, None),
    (FALLING_TAIL, None),
    (FLAT_TAIL, None),
    (FLAT_TAIL, 3),
    (None, 13),
)


def frames_for(candles=None):
    eligible = eligibility_chain(candles_for(candles) if candles is not None else None)
    return publish(real_engine(eligible), eligible)


def _config(horizon):
    return (
        OutcomeTrackingConfig() if horizon is None else OutcomeTrackingConfig(horizon_bars=horizon)
    )


@pytest.mark.parametrize(("candles", "horizon"), SCENARIOS)
def test_lifecycle_is_identical_to_the_manual_bridge(candles, horizon) -> None:
    config = _config(horizon)
    frames = frames_for(candles)

    # Path A: the Phase 26A hand-rolled bridge.
    observer_a, tracker_a, steps_a = manual_bridge(frames, config)

    # Path B: the Phase 26B lifecycle over the same frames and configuration.
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(config), OutcomeTrackingAnalyzer(config))
    steps_b = tuple(lifecycle.update(frame) for frame in frames)

    assert len(steps_b) == len(frames) == len(steps_a)
    assert observer_a.observations == lifecycle.observer.observations
    assert observer_a.open_outcomes == lifecycle.open_outcomes
    assert observer_a.finalized_outcomes == lifecycle.finalized_outcomes
    assert observer_a.strategy_stats == lifecycle.strategy_stats
    assert observer_a.monthly_report() == lifecycle.monthly_report()
    assert tracker_a.finalized_outcomes == lifecycle.tracker.finalized_outcomes
    for step_a, step_b in zip(steps_a, steps_b, strict=True):
        assert step_b.observation == step_a[0]
        assert step_b.outcome_snapshot == step_a[1]


@pytest.mark.parametrize(("candles", "horizon"), SCENARIOS)
def test_equivalence_holds_for_stats_and_monthly_details(candles, horizon) -> None:
    config = _config(horizon)
    frames = frames_for(candles)
    observer_a, _, _ = manual_bridge(frames, config)
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(config))

    run_lifecycle(frames, lifecycle)

    stats_a, stats_b = observer_a.strategy_stats, lifecycle.strategy_stats
    assert (stats_a.win_count, stats_a.loss_count, stats_a.flat_count) == (
        stats_b.win_count,
        stats_b.loss_count,
        stats_b.flat_count,
    )
    assert (stats_a.total_buy_signals, stats_a.open_count, stats_a.finalized_count) == (
        stats_b.total_buy_signals,
        stats_b.open_count,
        stats_b.finalized_count,
    )
    assert stats_a.win_rate == stats_b.win_rate
    assert stats_a.average_final_return == stats_b.average_final_return
    report_a, report_b = observer_a.monthly_report(), lifecycle.monthly_report()
    assert [month.month for month in report_a.months] == [month.month for month in report_b.months]
    for month_a, month_b in zip(report_a.months, report_b.months, strict=True):
        assert month_a.summary == month_b.summary


def test_batch_and_streaming_lifecycle_runs_agree() -> None:
    frames = frames_for(None)
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver())
    streaming = tuple(lifecycle.update(frame) for frame in frames)
    assert run_lifecycle(frames, SignalOutcomeLifecycle(AnalyticsObserver())) == streaming
    assert run_lifecycle(frames) == streaming  # default construction is deterministic
