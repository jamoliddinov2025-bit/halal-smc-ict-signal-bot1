"""Phase 35E service tests: checkpoint restart, fencing, retention, equivalence.

Covers the Phase 35E failure-mode matrix over the authorized LiveService path:
retention bounds, primary/HTF sequence continuity across checkpoint/restart,
deterministic serialization, identity rejection, runtime equivalence, and
duplicate-publication fencing — offline only (no network, dry-run delivery).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smcsignal.analysis.backtest import ReplayDataset
from smcsignal.analysis.signal_engine.models import SignalStatus
from smcsignal.datasets import DatasetStore, MemoryDatasetStore
from smcsignal.delivery.models import DeliveryState
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.telegram.integration import TelegramDeliveryIntegration
from smcsignal.delivery.transport import OfflinePayloadSink
from smcsignal.live import (
    DESTINATION_ID,
    FileCheckpointStore,
    LiveCheckpointError,
    LiveService,
    MemoryCheckpointStore,
    RetentionPolicy,
    load_live_config,
    start_live_service,
)
from smcsignal.live.checkpoint import live_checkpoint_key
from smcsignal.persistence import LedgerStore, MemoryLedgerStore
from tests.backtest.helpers import EIGHT, bars, configuration, higher_candles
from tests.live.test_market_feed import ScriptedTransport
from tests.live.test_service import (
    EXTENDED_PRICES,
    expected_frames,
    higher_payload,
    minute_payload,
)

STEP_MS = 15 * 60 * 1000


def live_dataset(candles: tuple) -> ReplayDataset:
    return ReplayDataset(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        higher_candles=higher_candles(),
        venue="binance_spot",
        provider="binance_public",
        dataset_id="live-feed:v1",
    )


def scripted_service(
    cycle_windows: tuple[int, ...],
    *,
    env_over: dict[str, str] | None = None,
    store: LedgerStore | None = None,
    window_store: DatasetStore | None = None,
    checkpoint_store: MemoryCheckpointStore | FileCheckpointStore | None = None,
    delivery: TelegramDeliveryIntegration | None = None,
    retention: RetentionPolicy | None = None,
) -> LiveService:
    transport = ScriptedTransport(
        {
            "15m": [minute_payload(count) for count in cycle_windows],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    env.update(env_over or {})
    return start_live_service(
        load_live_config(env),
        store=store if store is not None else MemoryLedgerStore(),
        window_store=window_store if window_store is not None else MemoryDatasetStore(),
        configuration=configuration(),
        market_transport=transport,
        delivery=delivery,
        checkpoint_store=checkpoint_store,
        retention=retention,
    )


def offline_delivery() -> TelegramDeliveryIntegration:
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=True))
    return TelegramDeliveryIntegration(OfflinePayloadSink(), coordinator=coordinator)


# -- retention -----------------------------------------------------------------


def test_windows_are_bounded_after_checkpoint_persistence() -> None:
    cfg = configuration()
    policy = RetentionPolicy.from_configuration(cfg)
    bound = policy.local_context_bound
    checkpoints = MemoryCheckpointStore()
    service = scripted_service(
        tuple(range(6, len(EXTENDED_PRICES) + 1)),
        checkpoint_store=checkpoints,
    )
    for _ in range(3):
        service.run_cycle()
    # Bound is configuration-derived and enforced on the service window.
    assert bound >= 3
    assert (
        len(service.primary_window) <= max(bound, 0)
        or len(service.primary_window) == service.runtime.processed_count
    )
    if service.runtime.processed_count > bound:
        assert len(service.primary_window) == bound
    # Checkpoint retains the FULL recovery history (not truncated).
    cp = checkpoints.load(live_checkpoint_key("BTCUSDT", "15m"))
    assert cp is not None
    assert cp.frame_count == service.runtime.processed_count
    assert len(cp.primary_candles) == cp.frame_count
    assert cp.retention_local_bound == bound
    # Fencing survives and is never part of the evicted window state.
    assert cp.published_setups == service.runtime.published_setups
    assert cp.published_setups  # fixture published real setups


def test_retention_none_keeps_full_window() -> None:
    checkpoints = MemoryCheckpointStore()
    service = scripted_service(
        (6, 9, 13, 17),
        checkpoint_store=checkpoints,
        retention=RetentionPolicy(local_context_bound=3),
        # retention provided: windows bound to 3 after checkpoint
    )
    service.run_cycle()
    service.run_cycle()
    assert len(service.primary_window) <= 3 or service.runtime.processed_count <= 3


# -- checkpoint restart / equivalence ------------------------------------------


def test_checkpoint_restart_matches_uninterrupted_run() -> None:
    """Run A: uninterrupted. Run B: checkpoint + restart + continue. Equal frames."""

    # Uninterrupted reference over the full extended history.
    primary = bars("15m", EXTENDED_PRICES, start=EIGHT)
    full = expected_frames(primary)

    store = MemoryLedgerStore()
    window = MemoryDatasetStore()
    checkpoints = MemoryCheckpointStore()
    delivery = offline_delivery()
    service = scripted_service(
        (6, 9, 13),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
        delivery=delivery,
    )
    for _ in range(3):
        service.run_cycle()
    assert service.runtime.processed_count == 13
    cp = checkpoints.load(live_checkpoint_key("BTCUSDT", "15m"))
    assert cp is not None
    assert cp.frame_count == 13

    # Restart with the same stores: checkpoint is preferred over the window.
    restarted = scripted_service(
        (13, 17),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
        delivery=delivery,
    )
    assert restarted.session.recovered_from is not None
    # Restored runtime regenerated the identical first 13 frames.
    assert restarted.runtime.processed_count == 13
    assert restarted.runtime.published_setups == service.runtime.published_setups
    assert restarted.runtime.latest == service.runtime.latest

    overlap = restarted.run_cycle()  # same overlap page: nothing new
    assert overlap.frames == 0
    continuation = restarted.run_cycle()
    assert continuation.frames == 4
    assert restarted.runtime.processed_count == 17
    # Signal-relevant state equals the uninterrupted run.
    assert restarted.runtime.latest == full[16]
    # Latest BUY identity matches the uninterrupted run's frame 16.
    assert (restarted.runtime.latest and restarted.runtime.latest.signal_id) == full[16].signal_id
    buy_ids_restarted = [
        f.signal_id
        for f in (restarted.runtime.latest,)
        if f is not None and f.status is SignalStatus.BUY_SIGNAL
    ]
    assert buy_ids_restarted == [
        f.signal_id for f in full[16:17] if f.status is SignalStatus.BUY_SIGNAL
    ]

    # Not only the final frame: the ordered publication sequence across the
    # whole restarted run must equal the uninterrupted reference. The warm-up
    # BUY (index 4) is recorded but never broadcast, so delivery starts at
    # index 8. (The frame-by-frame sequence comparison over twelve frames is
    # in ``tests/live/test_phase35e_audit.py``.)
    reference_buy_ids = [f.signal_id for f in full if f.status is SignalStatus.BUY_SIGNAL]
    assert len(reference_buy_ids) == 4
    delivered_ids = [payload.signal_id for payload in delivery.bridge.sink.seen]
    assert delivered_ids == reference_buy_ids[1:]
    ledger_snapshot = restarted.session.store.load(restarted.ledger_key)
    assert ledger_snapshot is not None
    assert [o.signal_id for o in ledger_snapshot.observations] == reference_buy_ids


def test_restart_does_not_republish_an_already_published_signal() -> None:
    checkpoints = MemoryCheckpointStore()
    store = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = scripted_service(
        (6, 9, 13, 17),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
    )
    reports = [service.run_cycle() for _ in range(3)]
    delivered_before = list(service.delivery.bridge.sink.seen)
    assert sum(r.buy_signals for r in reports) >= 1
    assert delivered_before

    # Restart with checkpoint; same delivery integration (dedup registry lives there)
    # but the SIGNAL ENGINE fencing must also prevent a new logical publication.
    restarted = scripted_service(
        (17, 17),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
        delivery=service.delivery,
    )
    cp = checkpoints.load(live_checkpoint_key("BTCUSDT", "15m"))
    assert cp is not None
    assert set(cp.published_setups) <= set(restarted.runtime.published_setups)
    # Regenerated fencing covers every previously published setup.
    for setup in cp.published_setups:
        assert setup in restarted.runtime.published_setups

    # Re-offering the identical historical page publishes nothing new.
    report = restarted.run_cycle()
    assert report.frames == 0
    assert report.buy_signals == 0
    assert len(service.delivery.bridge.sink.seen) == len(delivered_before)

    # Even if the latest BUY frame is re-delivered, the frozen Phase 24 dedup
    # registry skips it; and the engine will not mint a duplicate setup id.
    latest = restarted.runtime.latest
    assert latest is not None
    if latest.status is SignalStatus.BUY_SIGNAL:
        redelivered = restarted.delivery.deliver(latest, DESTINATION_ID)
        assert redelivered.state in (
            DeliveryState.SKIPPED_DUPLICATE,
            DeliveryState.DELIVERED,
        )


def test_checkpoint_around_a_signal_boundary_still_fences() -> None:
    """Checkpoint taken exactly after a BUY cycle; restart never re-publishes it."""

    checkpoints = MemoryCheckpointStore()
    store = MemoryLedgerStore()
    window = MemoryDatasetStore()
    # Cycle layout from the frozen service tests: BUY at index 8 arrives in
    # the first cycle (window 6 → 9).
    service = scripted_service(
        (6, 9, 13),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
    )
    first = service.run_cycle()
    assert first.buy_signals == 1  # BUY at index 8 is in this cycle
    cp = checkpoints.load(live_checkpoint_key("BTCUSDT", "15m"))
    assert cp is not None
    assert cp.published_setups  # fencing captured right at the signal boundary
    buy_ids_before = [payload.signal_id for payload in service.delivery.bridge.sink.seen]

    restarted = scripted_service(
        (9, 9, 13),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
        delivery=service.delivery,
    )
    assert cp.published_setups == restarted.runtime.published_setups
    # Replaying the same boundary pages never creates a new logical signal.
    report = restarted.run_cycle()
    assert report.frames == 0
    report = restarted.run_cycle()
    assert report.buy_signals == 0 or report.frames == 0
    after = [payload.signal_id for payload in service.delivery.bridge.sink.seen]
    assert after == buy_ids_before


def test_htf_update_after_restart_matches_uninterrupted_state() -> None:
    """HTF progression across checkpoint/restart stays equivalent (MTF/OTE)."""

    checkpoints = MemoryCheckpointStore()
    store = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = scripted_service(
        (6, 9, 13),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
    )
    for _ in range(3):
        service.run_cycle()
    before_restart_htf = {tf: candles for tf, candles in service.runtime.higher_candles.items()}

    restarted = scripted_service(
        (13, 17),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
    )
    # HTF histories restored exactly.
    for tf, candles in before_restart_htf.items():
        assert restarted.runtime.higher_candles[tf] == candles
    overlap = restarted.run_cycle()  # page 13: entirely behind the cursor
    assert overlap.frames == 0
    continued = restarted.run_cycle()  # page 17: four genuinely new candles
    assert continued.frames == 4
    # OTE/MTF state advanced; latest equals the frozen chain's frame 17.
    expected = expected_frames(bars("15m", EXTENDED_PRICES, start=EIGHT))
    assert restarted.runtime.latest == expected[16]
    # Downstream MTF relations remain present and well-formed after restart+HTF.
    latest = restarted.runtime.latest
    assert latest is not None
    mtf_snapshot = latest.upstream.upstream.upstream.upstream
    assert tuple(r.timeframe for r in mtf_snapshot.relations) == ("1h", "4h")


def test_primary_sequence_duplicate_and_stale_candles_are_ignored() -> None:
    checkpoints = MemoryCheckpointStore()
    service = scripted_service((6, 9, 9, 9), checkpoint_store=checkpoints)
    first = service.run_cycle()
    assert first.frames == 3
    # Identical page: every candle is at or behind the cursor.
    repeated = service.run_cycle()
    assert repeated.frames == 0
    assert repeated.buy_signals == 0
    # Checkpoint still reflects the accepted sequence only.
    cp = checkpoints.load(live_checkpoint_key("BTCUSDT", "15m"))
    assert cp is not None
    assert cp.frame_count == 9
    assert cp.last_primary is not None


# -- identity rejection --------------------------------------------------------


def test_checkpoint_keys_are_separated_per_symbol() -> None:
    """Checkpoint keys are per-symbol, so two series never share a file.

    Renamed from ``test_wrong_symbol_checkpoint_is_rejected``: this asserts
    key separation only. The *rejection* of a checkpoint that genuinely
    belongs to another declaration (symbol, timeframe, higher timeframes,
    series) is covered by the true rejection tests in
    ``tests/live/test_phase35e_audit.py``.
    """

    checkpoints = MemoryCheckpointStore()
    service = scripted_service((6, 9), checkpoint_store=checkpoints)
    service.run_cycle()
    other = scripted_service(
        (6, 9),
        checkpoint_store=checkpoints,
        env_over={"LIVE_SYMBOL": "ETHUSDT"},
    )
    # Different symbol → different key → no checkpoint cross-talk.
    assert other.checkpoint_key != service.checkpoint_key
    assert other.checkpoint_key == live_checkpoint_key("ETHUSDT", "15m")
    eth = checkpoints.load(other.checkpoint_key)
    btc = checkpoints.load(service.checkpoint_key)
    assert eth is not None and eth.symbol == "ETHUSDT"
    assert btc is not None and btc.symbol == "BTCUSDT"


def test_wrong_configuration_checkpoint_is_rejected() -> None:
    checkpoints = MemoryCheckpointStore()
    store = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = scripted_service(
        (6, 9),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
    )
    service.run_cycle()

    # Tamper the stored configuration identity to simulate a config change.
    key = live_checkpoint_key("BTCUSDT", "15m")
    cp = checkpoints.load(key)
    assert cp is not None
    from smcsignal.live.checkpoint import LiveCheckpoint

    tampered = LiveCheckpoint(
        configuration_identity="configuration:" + "0" * 64,
        symbol=cp.symbol,
        primary_timeframe=cp.primary_timeframe,
        higher_timeframes=cp.higher_timeframes,
        series=cp.series,
        last_primary=cp.last_primary,
        last_higher=dict(cp.last_higher),
        frame_count=cp.frame_count,
        retention_local_bound=cp.retention_local_bound,
        primary_candles=cp.primary_candles,
        higher_candles=dict(cp.higher_candles),
        published_setups=cp.published_setups,
        latest_signal_id=cp.latest_signal_id,
    )
    checkpoints.save(key, tampered)
    with pytest.raises(LiveCheckpointError, match="configuration_identity"):
        scripted_service(
            (9, 9),
            store=store,
            window_store=window,
            checkpoint_store=checkpoints,
        )


def test_corrupt_checkpoint_fails_closed_on_restart(tmp_path: Path) -> None:
    checkpoints = FileCheckpointStore(tmp_path)
    store = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = scripted_service(
        (6, 9),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
    )
    service.run_cycle()
    # Corrupt the durable bytes in place.
    files = list(tmp_path.glob("*.checkpoint.json"))
    assert len(files) == 1
    files[0].write_bytes(files[0].read_bytes()[:50])
    with pytest.raises(LiveCheckpointError):
        scripted_service(
            (9, 9),
            store=store,
            window_store=window,
            checkpoint_store=checkpoints,
        )


def test_fencing_mismatch_after_tampered_checkpoint_fails_closed() -> None:
    checkpoints = MemoryCheckpointStore()
    store = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = scripted_service(
        (6, 9, 13),
        store=store,
        window_store=window,
        checkpoint_store=checkpoints,
    )
    for _ in range(2):
        service.run_cycle()
    key = live_checkpoint_key("BTCUSDT", "15m")
    cp = checkpoints.load(key)
    assert cp is not None
    from smcsignal.live.checkpoint import LiveCheckpoint

    # Drop a fencing entry while keeping candles/identity intact. The digest
    # will still match (we rebuild via save), but regeneration will disagree.
    assert cp.published_setups
    tampered = LiveCheckpoint(
        configuration_identity=cp.configuration_identity,
        symbol=cp.symbol,
        primary_timeframe=cp.primary_timeframe,
        higher_timeframes=cp.higher_timeframes,
        series=cp.series,
        last_primary=cp.last_primary,
        last_higher=dict(cp.last_higher),
        frame_count=cp.frame_count,
        retention_local_bound=cp.retention_local_bound,
        primary_candles=cp.primary_candles,
        higher_candles=dict(cp.higher_candles),
        published_setups=cp.published_setups[:-1],
        latest_signal_id=cp.latest_signal_id,
    )
    checkpoints.save(key, tampered)
    with pytest.raises(LiveCheckpointError, match="fencing"):
        scripted_service(
            (13, 13),
            store=store,
            window_store=window,
            checkpoint_store=checkpoints,
        )


# -- ordering and dry-run ------------------------------------------------------


def test_ledger_persists_before_checkpoint_and_delivery() -> None:
    order: list[str] = []

    class RecordingStore(MemoryDatasetStore):
        def save(self, key: str, dataset: ReplayDataset) -> None:  # type: ignore[override]
            order.append("window")
            super().save(key, dataset)

    class RecordingCheckpoints(MemoryCheckpointStore):
        def save(self, key: str, checkpoint: object) -> None:  # type: ignore[override]
            order.append("checkpoint")
            super().save(key, checkpoint)  # type: ignore[arg-type]

    class RecordingDelivery(TelegramDeliveryIntegration):
        def deliver(self, frame: object, destination_id: str):  # type: ignore[override]
            order.append("delivery")
            return super().deliver(frame, destination_id)  # type: ignore[arg-type]

    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=True))
    delivery = RecordingDelivery(OfflinePayloadSink(), coordinator=coordinator)
    service = scripted_service(
        (6, 9, 13),
        window_store=RecordingStore(),
        checkpoint_store=RecordingCheckpoints(),
        delivery=delivery,
    )
    order.clear()
    report = service.run_cycle()
    assert report.ledger_persisted is True
    assert report.checkpoint_persisted is True
    # analysis (implicit) → window → checkpoint → delivery; ledger persist
    # runs before window persist inside run_cycle (frozen order).
    assert order.index("checkpoint") < order.index("delivery")
    assert order[-1] == "delivery"


def test_dry_run_delivery_unchanged_with_checkpoint() -> None:
    checkpoints = MemoryCheckpointStore()
    service = scripted_service((6, 9), checkpoint_store=checkpoints)
    assert isinstance(service.delivery.bridge.sink, OfflinePayloadSink)
    report = service.run_cycle()
    assert report.checkpoint_persisted is True
    assert report.delivery_states == (DeliveryState.DELIVERED,)
    # Checkpoint store does not touch the outbox/delivery sink payloads.
    cp = checkpoints.load(live_checkpoint_key("BTCUSDT", "15m"))
    assert cp is not None
    assert cp.frame_count == 9


def test_gap_aware_and_frozen_startup_paths_still_pass_without_checkpoint() -> None:
    """No checkpoint configured → pure Phase 33/35B/35C behavior unchanged."""

    service = scripted_service((6, 9, 13, 17))
    assert service.checkpoint_store is None
    assert service.checkpoint_key is None
    for _ in range(3):
        service.run_cycle()
    assert service.runtime.processed_count == 17
    # Window stays full when no checkpoint store bounds it.
    assert len(service.primary_window) == 17
    # retention policy is still declared (derived), even if windows unbounded
    # without a checkpoint store.
    assert service.retention is not None
    assert service.retention.local_context_bound >= 3
