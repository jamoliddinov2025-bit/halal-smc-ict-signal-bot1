"""Phase 26G: the single-series ledger session over the frozen arc.

The defining invariant: an uninterrupted run must equal persist -> restart ->
verified recovery -> continuation, for every split point and scenario, built
entirely on production seams (Phase 3-17 via 26F, 26B lifecycle, 26C bytes,
26D store, verified open). No tests/analytics helper generates frames here.
"""

from __future__ import annotations

import os

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import OutcomeTrackingConfig
from smcsignal.analysis.signal_engine.models import SignalStatus
from smcsignal.analytics import (
    AnalyticsObserver,
    SignalOutcomeLifecycle,
    ledger_bytes,
    snapshot_ledger,
)
from smcsignal.delivery import DeliveryCoordinator, DeliveryState
from smcsignal.delivery.sink import FakeTransportSink
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from smcsignal.series import series_frames
from smcsignal.sessions import LedgerSession, open_ledger_session
from tests.backtest.helpers import PRIMARY_PRICES, configuration, dataset
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL

KEY = "series-primary"
CONFIG = configuration().outcome_tracking

SCENARIOS = (
    ("rising", PRIMARY_PRICES),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)


def frames_for(prices: tuple) -> tuple:
    """Real Phase 3-17 publication frames via the Phase 26F seam."""

    return series_frames(dataset(prices=prices), configuration())


def uninterrupted(frames: tuple) -> SignalOutcomeLifecycle:
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(CONFIG))
    for frame in frames:
        lifecycle.update(frame)
    return lifecycle


def assert_ledgers_equal(left: SignalOutcomeLifecycle, right: SignalOutcomeLifecycle) -> None:
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


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileLedgerStore(tmp_path / "ledgers")
    return MemoryLedgerStore()


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_fresh_session_equals_uninterrupted_run_and_is_durable(store, label, prices) -> None:
    frames = frames_for(prices)
    session = open_ledger_session(store, KEY, CONFIG, frames)

    assert isinstance(session, LedgerSession)
    assert session.recovered_from is None  # fresh bootstrap, never silently recovered
    assert session.config == CONFIG
    assert session.key == KEY
    assert session.frames_verified == len(frames)
    assert_ledgers_equal(session.lifecycle, uninterrupted(frames))
    # Approved open-time persistence: the store already holds the ledger.
    assert store.load(KEY) == snapshot_ledger(session.lifecycle)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
@pytest.mark.parametrize("split_fraction", (0, 2, 1))
def test_restart_recovery_equals_uninterrupted_run(store, label, prices, split_fraction) -> None:
    frames = frames_for(prices)
    split = len(frames) // split_fraction if split_fraction else 0

    before_restart = open_ledger_session(store, KEY, CONFIG, frames[:split])
    assert before_restart.recovered_from is None

    after_restart = open_ledger_session(store, KEY, CONFIG, frames)
    assert after_restart.recovered_from is not None
    assert after_restart.recovered_from == snapshot_ledger(before_restart.lifecycle)
    assert after_restart.frames_verified == len(frames)
    assert_ledgers_equal(after_restart.lifecycle, uninterrupted(frames))


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_continuation_after_restart_equals_uninterrupted_run(store, label, prices) -> None:
    frames = frames_for(prices)
    split = len(frames) // 2

    open_ledger_session(store, KEY, CONFIG, frames[:split])
    session = open_ledger_session(store, KEY, CONFIG, frames[:split])
    assert session.recovered_from is not None
    for frame in frames[split:]:
        session.update(frame)
    session.persist()
    assert_ledgers_equal(session.lifecycle, uninterrupted(frames))

    # And a third restart over the now-advanced store agrees as well.
    third = open_ledger_session(store, KEY, CONFIG, frames)
    assert_ledgers_equal(third.lifecycle, uninterrupted(frames))


def test_restart_across_distinct_store_instances(tmp_path) -> None:
    """A real restart: a fresh FileLedgerStore over the same root recovers."""

    frames = frames_for(PRIMARY_PRICES)
    split = len(frames) // 2
    root = tmp_path / "ledgers"

    open_ledger_session(FileLedgerStore(root), KEY, CONFIG, frames[:split])
    session = open_ledger_session(FileLedgerStore(root), KEY, CONFIG, frames)
    assert session.recovered_from is not None
    assert_ledgers_equal(session.lifecycle, uninterrupted(frames))


def test_plateau_recovery_and_frames_verified_semantics(store) -> None:
    """The mandated plateau test: non-BUY frames leave the ledger unchanged.

    ``frames_verified`` counts the supplied history frames actually replayed
    during the verified reconstruction — it never identifies the persisted
    snapshot's original physical frame boundary, which plateau frames make
    non-unique. No Phase 26C change is introduced to resolve the ambiguity.
    """

    frames = frames_for(PRIMARY_PRICES)
    # Records land at BUY index 4; frames 5, 6, 7 are non-BUY plateau frames.
    assert all(frame.status is not SignalStatus.BUY_SIGNAL for frame in frames[5:8])

    # 1. Create the stored snapshot at original boundary 6 (inside the plateau).
    original = open_ledger_session(store, KEY, CONFIG, frames[:6])
    stored = original.persist()
    assert original.frames_verified == 6

    # 2-4. Restart over a longer history that ends inside the same plateau:
    # recovery verifies at the record landing point and catches up through
    # frames that mutate nothing.
    recovered = open_ledger_session(store, KEY, CONFIG, frames[:8])
    assert recovered.recovered_from == stored
    assert recovered.frames_verified == 8  # replayed frames, NOT the original boundary 6
    assert snapshot_ledger(recovered.lifecycle) == stored  # plateau catch-up is identity

    # 5-6. Continue: the result is exactly the uninterrupted run.
    for frame in frames[8:]:
        recovered.update(frame)
    assert_ledgers_equal(recovered.lifecycle, uninterrupted(frames))


def test_plateau_makes_the_original_boundary_non_unique() -> None:
    frames = frames_for(PRIMARY_PRICES)
    store_a, store_b = MemoryLedgerStore(), MemoryLedgerStore()

    session_at_five = open_ledger_session(store_a, "k", CONFIG, frames[:5])
    session_at_seven = open_ledger_session(store_b, "k", CONFIG, frames[:7])
    # Both boundaries sit in the plateau after the record at frame 4: the
    # complete snapshots are identical even though the boundaries differ.
    assert (
        snapshot_ledger(session_at_five.lifecycle).snapshot_id
        == snapshot_ledger(session_at_seven.lifecycle).snapshot_id
    )
    # Both snapshots verify and continue identically over the same history.
    resumed_a = open_ledger_session(store_a, "k", CONFIG, frames)
    resumed_b = open_ledger_session(store_b, "k", CONFIG, frames)
    assert_ledgers_equal(resumed_a.lifecycle, resumed_b.lifecycle)
    assert_ledgers_equal(resumed_a.lifecycle, uninterrupted(frames))


def test_recovery_refuses_a_different_configuration_atomically(store) -> None:
    frames = frames_for(PRIMARY_PRICES)
    open_ledger_session(store, KEY, CONFIG, frames[:8])
    before = store.load(KEY)

    foreign = OutcomeTrackingConfig(horizon_bars=3)
    with pytest.raises(AnalysisInputError, match="different outcome configuration"):
        open_ledger_session(store, KEY, foreign, frames)
    assert store.load(KEY) == before  # stored bytes completely unchanged

    session = open_ledger_session(store, KEY, CONFIG, frames)  # corrected config still works
    assert_ledgers_equal(session.lifecycle, uninterrupted(frames))


def test_recovery_refuses_wrong_history_atomically(store) -> None:
    frames = frames_for(PRIMARY_PRICES)
    open_ledger_session(store, KEY, CONFIG, frames[:16])  # 3 observations, 1 final
    before_bytes = ledger_bytes(store.load(KEY))

    # Same candles, same BUY positions, same declared configuration — but a
    # different series identity, so every record differs: counts trigger the
    # candidate comparison, yet only complete snapshot equality authorizes.
    foreign_series = series_frames(dataset(symbol="ETHUSDT"), configuration())
    with pytest.raises(AnalysisInputError, match="does not equal the replayed history"):
        open_ledger_session(store, KEY, CONFIG, foreign_series)
    assert ledger_bytes(store.load(KEY)) == before_bytes

    truncated = frames[:10]  # counts never reach the stored snapshot's counts
    with pytest.raises(AnalysisInputError, match="never reproduced the stored snapshot"):
        open_ledger_session(store, KEY, CONFIG, truncated)
    assert ledger_bytes(store.load(KEY)) == before_bytes

    session = open_ledger_session(store, KEY, CONFIG, frames)
    assert_ledgers_equal(session.lifecycle, uninterrupted(frames))


class RecordingStore:
    """Test-side LedgerStore recording every save call."""

    def __init__(self) -> None:
        self._inner = MemoryLedgerStore()
        self.saves: list[str] = []

    def save(self, key, snapshot) -> None:
        self.saves.append(key)
        self._inner.save(key, snapshot)

    def load(self, key):
        return self._inner.load(key)

    def contains(self, key) -> bool:
        return self._inner.contains(key)


def test_store_is_never_written_before_successful_verification() -> None:
    frames = frames_for(PRIMARY_PRICES)
    recording = RecordingStore()
    open_ledger_session(recording, KEY, CONFIG, frames[:8])
    assert recording.saves == [KEY]  # exactly the approved open-time write

    with pytest.raises(AnalysisInputError):
        open_ledger_session(recording, KEY, OutcomeTrackingConfig(horizon_bars=3), frames)
    with pytest.raises(AnalysisInputError):
        open_ledger_session(
            recording, KEY, CONFIG, series_frames(dataset(symbol="ETHUSDT"), configuration())
        )
    assert recording.saves == [KEY]  # failed opens never write


def test_update_performs_no_persistence_io(store) -> None:
    frames = frames_for(PRIMARY_PRICES)
    session = open_ledger_session(store, KEY, CONFIG, frames[:6])
    stored = store.load(KEY)

    session.update(frames[6])
    session.update(frames[7])
    assert store.load(KEY) == stored  # updates alone never touch the store

    saved = session.persist()
    assert saved == snapshot_ledger(session.lifecycle)
    assert store.load(KEY) == saved


def test_failed_save_keeps_previous_snapshot_and_session_usable(tmp_path, monkeypatch) -> None:
    store = FileLedgerStore(tmp_path / "ledgers")
    frames = frames_for(PRIMARY_PRICES)
    session = open_ledger_session(store, KEY, CONFIG, frames[:6])
    before = store.load(KEY)

    def raiser(src, dst):
        raise OSError("injected failure")

    session.update(frames[6])
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", raiser)
        with pytest.raises(OSError):
            session.persist()
    assert store.load(KEY) == before  # previous snapshot intact after failure

    session.persist()  # retry succeeds once the failure is gone
    assert store.load(KEY) == snapshot_ledger(session.lifecycle)


def test_frame_rejection_is_inherited_and_leaves_everything_intact(store) -> None:
    frames = frames_for(PRIMARY_PRICES)
    session = open_ledger_session(store, KEY, CONFIG, frames[:3])
    state = snapshot_ledger(session.lifecycle)
    stored = store.load(KEY)

    with pytest.raises(AnalysisInputError):
        session.update(frames[5])  # gap: candle index 3 and 4 never consumed
    with pytest.raises(AnalysisInputError):
        session.update(frames[2])  # replayed frame
    assert snapshot_ledger(session.lifecycle) == state
    assert store.load(KEY) == stored

    session.update(frames[3])  # the correct continuation still works
    assert session.lifecycle.processed_count == 4


def test_empty_history_session_and_recovery_from_empty_snapshot(store) -> None:
    frames = frames_for(PRIMARY_PRICES)
    empty = open_ledger_session(store, KEY, CONFIG, ())
    assert empty.recovered_from is None
    assert empty.frames_verified == 0
    assert empty.lifecycle.observer.observations == ()
    assert store.load(KEY) == snapshot_ledger(empty.lifecycle)

    # Recovery against the empty stored snapshot verifies before any frame.
    session = open_ledger_session(store, KEY, CONFIG, frames)
    assert session.recovered_from is not None
    assert session.recovered_from == snapshot_ledger(empty.lifecycle)
    assert session.frames_verified == len(frames)
    assert_ledgers_equal(session.lifecycle, uninterrupted(frames))


def test_parallel_delivery_never_moves_the_session_ledger(store) -> None:
    frames = frames_for(PRIMARY_PRICES)
    session = open_ledger_session(store, KEY, CONFIG, frames[:8])
    before = snapshot_ledger(session.lifecycle)

    for state in (DeliveryState.DELIVERED, DeliveryState.FAILED, DeliveryState.UNKNOWN):
        coordinator = DeliveryCoordinator(FakeTransportSink([state]))
        for frame in frames:
            if frame.status is SignalStatus.BUY_SIGNAL:
                assert coordinator.deliver(frame, "destination:phase26g").state is state

    assert snapshot_ledger(session.lifecycle) == before
    for frame in frames[8:]:
        session.update(frame)
    full = uninterrupted(frames)
    assert_ledgers_equal(session.lifecycle, full)
    assert session.lifecycle.strategy_stats.win_count == 1  # the market WIN, never delivery


def test_open_input_validation_never_defaults_configuration(store) -> None:
    frames = frames_for(PRIMARY_PRICES)
    with pytest.raises(AnalysisInputError, match="never defaulted"):
        open_ledger_session(store, KEY, None, frames)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="never defaulted"):
        open_ledger_session(store, KEY, configuration(), frames)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="iterable of SignalSnapshot"):
        open_ledger_session(store, KEY, CONFIG, 42)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="SignalSnapshot"):
        open_ledger_session(store, KEY, CONFIG, (42,))  # type: ignore[arg-type]
    assert store.contains(KEY) is False  # no partial session ever persisted


def test_session_holder_validation() -> None:
    frames = frames_for(PRIMARY_PRICES)
    store = MemoryLedgerStore()
    session = open_ledger_session(store, KEY, CONFIG, frames[:6])
    snapshot = store.load(KEY)
    assert snapshot is not None

    with pytest.raises(AnalysisInputError, match="SignalOutcomeLifecycle"):
        LedgerSession(snapshot, store, KEY, CONFIG, None, 0)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="OutcomeTrackingConfig"):
        LedgerSession(session.lifecycle, store, KEY, None, None, 0)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="declared config"):
        LedgerSession(session.lifecycle, store, KEY, OutcomeTrackingConfig(horizon_bars=3), None, 0)
    with pytest.raises(AnalysisInputError, match="LedgerSnapshot or None"):
        LedgerSession(session.lifecycle, store, KEY, CONFIG, frames[0], 0)  # type: ignore[arg-type]
    foreign_lifecycle = SignalOutcomeLifecycle(
        AnalyticsObserver(OutcomeTrackingConfig(horizon_bars=3))
    )
    with pytest.raises(AnalysisInputError, match="declared configuration"):
        LedgerSession(
            foreign_lifecycle,
            store,
            KEY,
            OutcomeTrackingConfig(horizon_bars=3),
            snapshot,
            0,
        )
    with pytest.raises(AnalysisInputError, match="nonnegative integer"):
        LedgerSession(session.lifecycle, store, KEY, CONFIG, None, -1)
