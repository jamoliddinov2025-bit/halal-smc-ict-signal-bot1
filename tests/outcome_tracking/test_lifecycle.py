from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis import (
    OutcomeStatus,
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
    SignalStatus,
    analyze_outcome_tracking,
)
from tests.outcome_tracking.helpers import (
    buy_indices,
    completed_records,
    outcome_for,
    run,
    signal_frames,
)


def test_buy_frames_open_exactly_one_outcome() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    buys = buy_indices(frames)
    assert buys == [4, 8, 12, 16]
    for index in buys:
        created = snapshots[index].created
        assert len(created) == 1
        record = created[0]
        assert record.status is OutcomeStatus.OPEN
        assert record.candles_observed == 0
        assert record.reference.candle_index == index
        assert record.reference_close == Decimal(20 + index)
        assert record.mfe_price is None and record.mae_price is None
        assert record.final_close is None and record.final_index is None
    for index in range(len(frames)):
        if index not in buys:
            assert snapshots[index].created == ()


def test_non_buy_statuses_never_create_outcomes() -> None:
    snapshots = run()
    statuses = {snapshot.upstream.status for snapshot in snapshots}
    assert statuses <= {SignalStatus.BUY_SIGNAL, SignalStatus.NO_SIGNAL, SignalStatus.BEARISH_AVOID}
    for snapshot in snapshots:
        for _record in snapshot.created:
            assert snapshot.upstream.status is SignalStatus.BUY_SIGNAL


def test_outcome_progresses_one_version_per_evaluation_candle() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    versions = outcome_for(snapshots, frames[4].signal_id)
    assert [version.candles_observed for version in versions] == list(range(11))
    for offset, version in enumerate(versions):
        assert version.reference.candle_index == 4
        if offset == 0:
            assert version.status is OutcomeStatus.OPEN
        elif offset < 10:
            assert version.status is OutcomeStatus.OPEN
            assert version.final_close is None
        else:
            assert version.status is OutcomeStatus.WIN
    assert [record.candles_observed for record in snapshots[5].evaluated] == [1]
    assert snapshots[4].evaluated == ()


def test_finalization_happens_exactly_at_the_horizon_candle() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames, config=OutcomeTrackingConfig(horizon_bars=8))
    completed = completed_records(snapshots)
    assert [(record.reference.candle_index, record.final_index) for record in completed] == [
        (4, 12),
        (8, 16),
    ]
    for record in completed:
        assert record.candles_observed == record.horizon_bars == 8
        assert record.final_index == record.reference.candle_index + 8


def test_completed_versions_are_never_touched_again() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    final = completed_records(snapshots)[0]
    assert final.status is OutcomeStatus.WIN
    later_evaluated = {
        record.outcome_id
        for snapshot in snapshots[final.final_index + 1 :]
        for record in snapshot.evaluated
    }
    assert final.outcome_id not in later_evaluated
    assert snapshots[final.final_index].completed == (final,)
    still_open = {record.outcome_id for record in snapshots[final.final_index + 1].evaluated}
    assert final.outcome_id not in still_open and still_open


def test_open_outcomes_stay_open_at_end_of_series() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    last = snapshots[-1]
    assert last.analytics.open_count == 3
    assert last.analytics.finalized_count == 1
    open_ids = {
        record.outcome_id for record in last.evaluated if record.status is OutcomeStatus.OPEN
    }
    assert len(open_ids) == 2
    public = {name for name in dir(OutcomeTrackingAnalyzer) if not name.startswith("_")}
    assert public == {
        "config",
        "latest",
        "processed_count",
        "series",
        "configuration_artifact",
        "open_outcomes",
        "finalized_outcomes",
        "update",
    }


def test_horizon_beyond_series_keeps_everyone_open() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames, config=OutcomeTrackingConfig(horizon_bars=13))
    assert buy_indices(frames) == [4, 8, 12, 16]
    assert completed_records(snapshots) == []
    last = snapshots[-1].analytics
    assert last.total_buy_signals == 4
    assert last.open_count == 4
    assert last.finalized_count == 0
    assert last.win_rate is None


def test_buy_on_the_last_frame_opens_with_zero_observations() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    created = snapshots[-1].created
    assert snapshots[-1].upstream.status is SignalStatus.BUY_SIGNAL
    assert len(created) == 1
    assert created[0].candles_observed == 0
    assert created[0].status is OutcomeStatus.OPEN
    assert created[0].mfe_price is None and created[0].mae_price is None
    assert len(snapshots[-1].evaluated) == 2
    assert snapshots[4].evaluated == ()


def test_one_frame_can_complete_and_create_simultaneously() -> None:
    frames = signal_frames()
    snapshots = run(
        frames=frames,
        config=OutcomeTrackingConfig(horizon_bars=4),
    )
    snapshot = snapshots[8]
    assert snapshot.upstream.status is SignalStatus.BUY_SIGNAL
    assert snapshot.completed and snapshot.created
    assert [record.reference.candle_index for record in snapshot.completed] == [4]
    assert snapshot.created[0].reference.candle_index == 8
    analytics = snapshot.analytics
    assert analytics.finalized_count == 1 and analytics.open_count == 1


def test_signal_candle_is_never_its_own_evaluation_candle() -> None:
    frames = signal_frames()
    snapshots = run(
        frames=frames,
        config=OutcomeTrackingConfig(horizon_bars=1),
    )
    completed = completed_records(snapshots)
    for record in completed:
        assert record.final_index == record.reference.candle_index + 1
        assert record.candles_observed == 1
    assert {record.status for record in completed} == {OutcomeStatus.WIN}
    assert [str(record.final_close) for record in completed] == ["25", "29", "33"]


def test_continuation_after_a_pause_completes_the_outcome() -> None:
    frames = signal_frames()
    config = OutcomeTrackingConfig(horizon_bars=10)
    partial = analyze_outcome_tracking(frames[:9], config)
    assert partial[-1].analytics.open_count == 2
    resumed = analyze_outcome_tracking(frames, config)
    assert resumed[:9] == partial
    completed = completed_records(resumed)
    assert [record.status for record in completed] == [OutcomeStatus.WIN]


def test_lifecycle_transitions_are_open_then_final_only() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    for signal_id in {
        frame.signal_id for frame in frames if frame.status is SignalStatus.BUY_SIGNAL
    }:
        versions = outcome_for(snapshots, signal_id)
        statuses = [version.status for version in versions]
        finals = [status for status in statuses if status is not OutcomeStatus.OPEN]
        assert finals in ([], [OutcomeStatus.WIN], [OutcomeStatus.LOSS], [OutcomeStatus.FLAT])
        assert statuses[: len(statuses) - len(finals)] == [OutcomeStatus.OPEN] * (
            len(statuses) - len(finals)
        )
