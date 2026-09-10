"""Phase 26B lifecycle flow: publication → OPEN → horizon evaluation → finals.

All frames come from the real Phase 3-17 chain. The lifecycle must finalize
nothing before the Phase 18 horizon, never read a future candle, reject
sequence violations atomically, keep the ledger untouched by parallel delivery
state, and refuse any composition that could mix series or inherit untracked
finals.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import (
    OutcomeStatus,
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
)
from smcsignal.analytics import AnalyticsObserver, SignalOutcomeLifecycle, run_lifecycle
from smcsignal.delivery import DeliveryCoordinator, DeliveryState
from smcsignal.delivery.sink import FakeTransportSink
from tests.analytics.helpers import (
    buy_frames,
    eligibility_chain,
    publish,
    real_engine,
)

HORIZON = 10  # OutcomeTrackingConfig default


def frames_for(candles=None):
    eligible = eligibility_chain(candles)
    return publish(real_engine(eligible), eligible)


def ledger(lifecycle):
    return (
        lifecycle.observer.observations,
        lifecycle.open_outcomes,
        lifecycle.finalized_outcomes,
        lifecycle.strategy_stats,
        lifecycle.processed_count,
    )


def test_lifecycle_observes_and_auto_finalizes_real_publications() -> None:
    frames = frames_for()
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver())
    buys = buy_frames(frames)
    assert len(buys) == 4

    steps = run_lifecycle(frames, lifecycle)
    assert sum(1 for step in steps if step.observation is not None) == 4
    for frame, step in zip(frames, steps, strict=True):
        assert (step.observation is not None) is (frame in buys)

    assert [record.status for record in lifecycle.finalized_outcomes] == [OutcomeStatus.WIN]
    stats = lifecycle.strategy_stats
    assert (stats.finalized_count, stats.win_count, stats.open_count) == (1, 1, 3)
    report = lifecycle.monthly_report()
    assert report.totals == stats


def test_step_binds_identical_observer_and_evaluator_records() -> None:
    frames = frames_for()
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver())
    for frame in frames:
        step = lifecycle.update(frame)
        if frame in buy_frames(frames):
            assert step.observation is not None
            assert step.observation.signal_id == frame.signal_id
            assert step.outcome_snapshot.created == (step.observation.outcome,)
        else:
            assert step.observation is None
            assert step.outcome_snapshot.created == ()


def test_no_finalization_before_horizon_and_no_lookahead() -> None:
    frames = frames_for()
    buys = buy_frames(frames)
    publish_indices = {frame.signal_id: index for index, frame in enumerate(frames)}
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver())

    for index, frame in enumerate(frames):
        lifecycle.update(frame)
        for record in lifecycle.finalized_outcomes:
            # A final references only candles at or before the consumed frame:
            # no evaluation of a future candle ever exists at any step.
            assert record.final_index <= index
            assert record.final_index == publish_indices[record.signal_id] + HORIZON
            assert record.reference.candle_index < index
            low, high = record.reference.candle_index, index
            assert low < record.mfe_index <= high
            assert low < record.mae_index <= high

    finalized_signals = {record.signal_id for record in lifecycle.finalized_outcomes}
    for frame in buys:
        expected_final_frame = publish_indices[frame.signal_id] + HORIZON
        if expected_final_frame < len(frames):
            assert frame.signal_id in finalized_signals
        else:
            # The horizon candles were never observed: the outcome stays OPEN.
            assert frame.signal_id not in finalized_signals
    assert len(finalized_signals) == 1  # only the index-4 BUY reaches its horizon


def test_replayed_buy_frame_is_rejected_atomically() -> None:
    frames = frames_for()
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver())
    for frame in frames[:6]:
        lifecycle.update(frame)
    state = ledger(lifecycle)

    with pytest.raises(AnalysisInputError):
        lifecycle.update(frames[4])  # a BUY frame: even idempotent observation
        # must not occur, because the evaluator rejects the replay first
    assert ledger(lifecycle) == state
    assert lifecycle.observer.observations == state[0]


def test_gap_in_sequence_is_rejected_with_ledger_intact() -> None:
    frames = frames_for()
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver())
    lifecycle.update(frames[0])
    lifecycle.update(frames[1])
    state = ledger(lifecycle)
    with pytest.raises(AnalysisInputError):
        lifecycle.update(frames[3])  # skips candle index 2
    assert ledger(lifecycle) == state


def test_parallel_delivery_never_moves_the_lifecycle() -> None:
    frames = frames_for()
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver())
    run_lifecycle(frames, lifecycle)
    before = ledger(lifecycle)

    for state in (DeliveryState.DELIVERED, DeliveryState.FAILED, DeliveryState.UNKNOWN):
        coordinator = DeliveryCoordinator(FakeTransportSink([state]))
        for frame in buy_frames(frames):
            assert coordinator.deliver(frame, "destination:phase26b").state is state

    assert ledger(lifecycle) == before
    assert lifecycle.strategy_stats.win_count == 1  # the market WIN, never delivery


def test_constructor_guards_pin_single_series_fresh_composition() -> None:
    frames = frames_for()
    with pytest.raises(AnalysisInputError, match="fresh observer"):
        used_observer = AnalyticsObserver()
        used_observer.observe(buy_frames(frames)[0])
        SignalOutcomeLifecycle(used_observer)

    with pytest.raises(AnalysisInputError, match="fresh evaluator"):
        used_tracker = OutcomeTrackingAnalyzer()
        used_tracker.update(frames[0])
        SignalOutcomeLifecycle(AnalyticsObserver(), used_tracker)

    with pytest.raises(AnalysisInputError, match="share one OutcomeTrackingConfig"):
        SignalOutcomeLifecycle(
            AnalyticsObserver(OutcomeTrackingConfig(horizon_bars=5)),
            OutcomeTrackingAnalyzer(OutcomeTrackingConfig(horizon_bars=7)),
        )

    with pytest.raises(AnalysisInputError, match="requires an AnalyticsObserver"):
        SignalOutcomeLifecycle(frames[0])  # type: ignore[arg-type]
