"""Phase 26E: verified replay-based ledger recovery and continuation.

The defining invariant: an uninterrupted run of the real Phase 26B lifecycle
must equal a split run — partial run -> Phase 26C snapshot -> canonical bytes
-> restore -> Phase 26E recovery -> continuation. Every frame here is
regenerated through the real Phase 3-17 chain and driven through the real
26B lifecycle; nothing is handcrafted.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import OutcomeTrackingConfig
from smcsignal.analysis.signal_engine.models import SignalStatus
from smcsignal.analytics import (
    AnalyticsObserver,
    RecoveredLedger,
    SignalOutcomeLifecycle,
    ledger_bytes,
    load_ledger_bytes,
    recover_lifecycle,
    snapshot_ledger,
)
from smcsignal.delivery import DeliveryCoordinator, DeliveryState
from smcsignal.delivery.sink import FakeTransportSink
from smcsignal.persistence import FileLedgerStore
from tests.analytics.helpers import buy_frames
from tests.analytics.test_ledger import SCENARIOS, frames_for, lifecycle_for
from tests.outcome_tracking.helpers import FALLING_TAIL, FLAT_TAIL


def scenario_frames(label: str, candles: object) -> tuple:
    """The scenario's historical frames; empty exactly for the empty scenario."""

    return frames_for(candles) if label != "empty" else ()


def config_for(horizon: int | None) -> OutcomeTrackingConfig:
    return (
        OutcomeTrackingConfig() if horizon is None else OutcomeTrackingConfig(horizon_bars=horizon)
    )


def build_lifecycle(frames: tuple, horizon: int | None = None) -> SignalOutcomeLifecycle:
    """A fresh real 26B lifecycle fed the given frames in order."""

    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(config_for(horizon)))
    for frame in frames:
        lifecycle.update(frame)
    return lifecycle


def round_tripped(lifecycle: SignalOutcomeLifecycle):
    """The lifecycle's snapshot through the exact Phase 26C bytes round trip."""

    return load_ledger_bytes(ledger_bytes(snapshot_ledger(lifecycle)))


def assert_ledgers_equal(left: SignalOutcomeLifecycle, right: SignalOutcomeLifecycle) -> None:
    """Full-ledger equality: records, views, statistics, reports, bytes, identity."""

    assert left.observer.observations == right.observer.observations
    assert left.open_outcomes == right.open_outcomes
    assert left.finalized_outcomes == right.finalized_outcomes
    assert left.strategy_stats == right.strategy_stats
    assert left.monthly_report() == right.monthly_report()
    left_snapshot = snapshot_ledger(left)
    right_snapshot = snapshot_ledger(right)
    assert left_snapshot == right_snapshot
    assert left_snapshot.snapshot_id == right_snapshot.snapshot_id
    assert ledger_bytes(left_snapshot) == ledger_bytes(right_snapshot)


@pytest.mark.parametrize(("label", "candles", "horizon"), SCENARIOS)
def test_split_restart_equals_uninterrupted_run(label, candles, horizon) -> None:
    frames = scenario_frames(label, candles)
    split = len(frames) // 2
    full = build_lifecycle(frames, horizon)

    partial = build_lifecycle(frames[:split], horizon)
    expected = round_tripped(partial)
    recovered = recover_lifecycle(frames[:split], expected)

    assert isinstance(recovered, RecoveredLedger)
    assert isinstance(recovered.lifecycle, SignalOutcomeLifecycle)
    assert recovered.frames_verified == split
    assert recovered.verified_snapshot == snapshot_ledger(partial)
    assert recovered.verified_snapshot.snapshot_id == snapshot_ledger(partial).snapshot_id
    assert snapshot_ledger(recovered.lifecycle) == expected

    for frame in frames[split:]:
        recovered.lifecycle.update(frame)
    assert_ledgers_equal(recovered.lifecycle, full)


@pytest.mark.parametrize(("label", "candles", "horizon"), SCENARIOS)
def test_recovery_preserves_every_ledger_view(label, candles, horizon) -> None:
    frames = scenario_frames(label, candles)
    full = build_lifecycle(frames, horizon)
    recovered = recover_lifecycle(frames, round_tripped(full))

    observer = full.observer
    assert recovered.lifecycle.observer.observations == observer.observations
    assert recovered.lifecycle.open_outcomes == full.open_outcomes
    assert recovered.lifecycle.finalized_outcomes == full.finalized_outcomes
    assert recovered.lifecycle.observer.config == observer.config
    assert recovered.lifecycle.observer.configuration_hash == observer.configuration_hash
    assert recovered.verified_snapshot.settings == observer.config
    assert recovered.verified_snapshot.configuration_hash == observer.configuration_hash


def test_recovery_holds_at_every_split_point() -> None:
    frames = frames_for(FLAT_TAIL)
    horizon = 3
    full = build_lifecycle(frames, horizon)
    for split in range(len(frames) + 1):
        partial = build_lifecycle(frames[:split], horizon)
        recovered = recover_lifecycle(frames[:split], round_tripped(partial))
        assert recovered.frames_verified == split
        for frame in frames[split:]:
            recovered.lifecycle.update(frame)
        assert_ledgers_equal(recovered.lifecycle, full)


@pytest.mark.parametrize(
    ("label", "candles", "horizon"),
    (("win", None, None), ("mixed", FLAT_TAIL, 3)),
)
def test_recovery_from_the_phase_26d_file_store(label, candles, horizon, tmp_path) -> None:
    """The full durable chain: 26B -> 26C bytes -> 26D store -> 26E recovery."""

    frames = scenario_frames(label, candles)
    split = len(frames) // 2
    full = build_lifecycle(frames, horizon)

    store = FileLedgerStore(tmp_path / "ledgers")
    partial = build_lifecycle(frames[:split], horizon)
    store.save("series-primary", snapshot_ledger(partial))

    expected = store.load("series-primary")
    assert expected is not None
    recovered = recover_lifecycle(frames[:split], expected)
    for frame in frames[split:]:
        recovered.lifecycle.update(frame)
    assert_ledgers_equal(recovered.lifecycle, full)


def test_continuation_observes_new_publications_and_finalizes_them() -> None:
    frames = frames_for(None)
    split = next(
        index for index, frame in enumerate(frames) if frame.status is SignalStatus.BUY_SIGNAL
    )
    partial = build_lifecycle(frames[:split])
    recovered = recover_lifecycle(frames[:split], round_tripped(partial))
    assert recovered.lifecycle.observer.observations == ()

    step = recovered.lifecycle.update(frames[split])
    assert step.observation is not None
    assert recovered.lifecycle.observer.observations == (step.observation,)
    assert recovered.lifecycle.open_outcomes == (step.observation.outcome,)

    for frame in frames[split + 1 :]:
        recovered.lifecycle.update(frame)
    full = build_lifecycle(frames)
    assert_ledgers_equal(recovered.lifecycle, full)
    assert recovered.lifecycle.strategy_stats.win_count == 1  # the market WIN, never anything else


def test_finals_after_recovery_complete_exactly_at_the_horizon() -> None:
    frames = frames_for(FLAT_TAIL)
    horizon = 3
    split = len(frames) // 2
    partial = build_lifecycle(frames[:split], horizon)
    recovered = recover_lifecycle(frames[:split], round_tripped(partial))

    completed_during_continuation = 0
    for frame in frames[split:]:
        step = recovered.lifecycle.update(frame)
        for record in step.outcome_snapshot.completed:
            completed_during_continuation += 1
            assert recovered.lifecycle.processed_count == record.final_index + 1
    full = build_lifecycle(frames, horizon)
    assert completed_during_continuation == len(full.finalized_outcomes) - len(
        partial.finalized_outcomes
    )
    assert completed_during_continuation >= 1  # continuation itself finalizes real outcomes


def test_recovery_rejects_a_stale_snapshot_atomically() -> None:
    frames = frames_for(None)
    split = len(frames) // 2
    stale = round_tripped(build_lifecycle(frames))  # taken later than the supplied history
    with pytest.raises(AnalysisInputError, match="does not equal the expected snapshot"):
        recover_lifecycle(frames[:split], stale)

    # The failure is atomic and deterministic: the correct pair still recovers.
    recovered = recover_lifecycle(frames[:split], round_tripped(build_lifecycle(frames[:split])))
    assert recovered.frames_verified == split


def test_recovery_rejects_snapshot_from_different_frame_history() -> None:
    win_snapshot = round_tripped(lifecycle_for("win"))
    loss_frames = frames_for(FALLING_TAIL)
    with pytest.raises(AnalysisInputError, match="does not equal the expected snapshot"):
        recover_lifecycle(loss_frames, win_snapshot)
    with pytest.raises(AnalysisInputError, match="does not equal the expected snapshot"):
        recover_lifecycle(frames_for(None), round_tripped(lifecycle_for("loss", FALLING_TAIL)))


@pytest.mark.parametrize(
    ("expected_label", "expected_candles", "expected_horizon", "frames_candles", "frames_horizon"),
    (
        ("win", None, None, FLAT_TAIL, 3),
        ("mixed", FLAT_TAIL, 3, None, None),
        ("loss", FALLING_TAIL, None, FLAT_TAIL, 3),
    ),
)
def test_recovery_rejects_snapshots_from_different_scenarios(
    expected_label, expected_candles, expected_horizon, frames_candles, frames_horizon
) -> None:
    """A snapshot verified under one scenario cannot validate another's history."""

    expected = round_tripped(lifecycle_for(expected_label, expected_candles, expected_horizon))
    with pytest.raises(AnalysisInputError, match="does not equal the expected snapshot"):
        recover_lifecycle(frames_for(frames_candles), expected)


def test_configuration_is_derived_from_the_expected_snapshot() -> None:
    frames = frames_for(None)
    expected = round_tripped(build_lifecycle(frames, horizon=8))
    recovered = recover_lifecycle(frames, expected)
    assert recovered.lifecycle.observer.config == OutcomeTrackingConfig(horizon_bars=8)
    assert recovered.lifecycle.observer.config.horizon_bars == 8


def test_recovery_rejects_replayed_out_of_order_and_gapped_history() -> None:
    frames = frames_for(None)
    expected = round_tripped(build_lifecycle(frames))

    gapped = frames[:2] + frames[3:]
    replayed = frames[:3] + (frames[2],) + frames[3:]
    out_of_order = (frames[1], frames[0]) + frames[2:]
    for malformed in (gapped, replayed, out_of_order):
        with pytest.raises(AnalysisInputError):
            recover_lifecycle(malformed, expected)

    # Sequence violations fail inside the frozen 26B/18 machinery; afterwards
    # the correct history still recovers — no state survives a failed attempt.
    recovered = recover_lifecycle(frames, expected)
    assert recovered.frames_verified == len(frames)


def test_empty_ledger_recovery_and_continuation() -> None:
    empty_snapshot = round_tripped(lifecycle_for("empty"))
    recovered = recover_lifecycle((), empty_snapshot)
    assert recovered.frames_verified == 0
    assert snapshot_ledger(recovered.lifecycle) == empty_snapshot
    assert recovered.lifecycle.observer.observations == ()

    frames = frames_for(None)
    for frame in frames:
        recovered.lifecycle.update(frame)
    assert_ledgers_equal(recovered.lifecycle, build_lifecycle(frames))

    with pytest.raises(AnalysisInputError, match="does not equal the expected snapshot"):
        recover_lifecycle(frames, empty_snapshot)


def test_recovery_is_deterministic_across_repeated_runs() -> None:
    frames = frames_for(FLAT_TAIL)
    expected = round_tripped(build_lifecycle(frames, 3))
    first = recover_lifecycle(frames, expected)
    second = recover_lifecycle(frames, expected)

    assert first.frames_verified == second.frames_verified
    assert first.verified_snapshot == second.verified_snapshot == expected
    assert_ledgers_equal(first.lifecycle, second.lifecycle)


def test_parallel_delivery_never_moves_a_recovered_ledger() -> None:
    frames = frames_for(None)
    split = len(frames) // 2
    recovered = recover_lifecycle(frames[:split], round_tripped(build_lifecycle(frames[:split])))
    before = snapshot_ledger(recovered.lifecycle)

    for state in (DeliveryState.DELIVERED, DeliveryState.FAILED, DeliveryState.UNKNOWN):
        coordinator = DeliveryCoordinator(FakeTransportSink([state]))
        for frame in buy_frames(frames):
            assert coordinator.deliver(frame, "destination:phase26e").state is state

    assert snapshot_ledger(recovered.lifecycle) == before
    for frame in frames[split:]:
        recovered.lifecycle.update(frame)
    full = build_lifecycle(frames)
    assert_ledgers_equal(recovered.lifecycle, full)
    assert recovered.lifecycle.strategy_stats.win_count == 1  # the market WIN, never delivery


def test_recovered_ledger_holder_is_self_verifying() -> None:
    frames = frames_for(None)
    full = build_lifecycle(frames)
    snapshot = round_tripped(full)
    holder = RecoveredLedger(full, snapshot, len(frames))
    assert holder.lifecycle is full
    assert holder.verified_snapshot == snapshot
    assert holder.frames_verified == len(frames)

    stale = round_tripped(build_lifecycle(frames[: len(frames) // 2]))
    with pytest.raises(AnalysisInputError, match="must be verified"):
        RecoveredLedger(full, stale, len(frames))
    with pytest.raises(AnalysisInputError, match="SignalOutcomeLifecycle"):
        RecoveredLedger(snapshot, snapshot, 0)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="LedgerSnapshot"):
        RecoveredLedger(full, full, 0)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="nonnegative integer"):
        RecoveredLedger(build_lifecycle(()), round_tripped(lifecycle_for("empty")), -1)
    with pytest.raises(AnalysisInputError, match="nonnegative integer"):
        RecoveredLedger(build_lifecycle(()), round_tripped(lifecycle_for("empty")), 1.0)  # type: ignore[arg-type]


def test_recovery_input_validation() -> None:
    snapshot = round_tripped(lifecycle_for("empty"))
    with pytest.raises(AnalysisInputError, match="Phase 26C LedgerSnapshot"):
        recover_lifecycle((), snapshot.observations[0:0])  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="iterable of SignalSnapshot"):
        recover_lifecycle(42, snapshot)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="SignalSnapshot"):
        recover_lifecycle((42,), snapshot)  # type: ignore[arg-type]
