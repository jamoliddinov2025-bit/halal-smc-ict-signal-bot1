"""Phase 35E audit remediation tests: C1, T1, T2, T3, and the documented C4 path.

Each test targets one named audit finding, and each one drives the *real*
Phase 35E seam rather than a re-implementation of it:

* **C1** — the service's schema-version gate must actually fire. The audited
  code compared ``checkpoint.schema_version`` with itself, so it could never
  reject anything. These tests hand the service a checkpoint whose declared
  schema version differs from the one this build implements, and require a
  ``LiveCheckpointError``.
* **T1** — true rejection tests for symbol, primary-timeframe,
  higher-timeframe, and series mismatches. Each one loads a checkpoint that
  genuinely belongs to another declaration and asserts the restore is
  refused, naming the offending field(s).
* **T2** — restart equivalence compared as an *output sequence* over many
  frames, not as final-frame equality.
* **T3** — a checkpoint persistence failure must abort the cycle after ledger
  persistence and before any delivery.
* **C4** — the lost-checkpoint / bounded-window fallback, documented in
  ``service.py`` and pinned here: fail closed when the persisted ledger has
  content, and measurably *not* detectable when it does not.

Offline only: no socket is opened and delivery always runs over the existing
``OfflinePayloadSink``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.signal_engine.models import SignalSnapshot
from smcsignal.datasets import MemoryDatasetStore
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.telegram.integration import TelegramDeliveryIntegration
from smcsignal.delivery.transport import OfflinePayloadSink
from smcsignal.live import (
    FileCheckpointStore,
    LiveCheckpoint,
    LiveCheckpointError,
    LiveService,
    MemoryCheckpointStore,
    RetentionPolicy,
    checkpoint_bytes,
    load_live_config,
    start_live_service,
)
from smcsignal.live.checkpoint import CHECKPOINT_SCHEMA_VERSION, live_checkpoint_key
from smcsignal.persistence import MemoryLedgerStore
from tests.backtest.helpers import EIGHT, bars, configuration, higher_candles
from tests.live.test_market_feed import ScriptedTransport
from tests.live.test_service import (
    EXTENDED_PRICES,
    expected_frames,
    higher_payload,
    minute_payload,
)

CHECKPOINT_KEY = live_checkpoint_key("BTCUSDT", "15m")
LEDGER_KEY = "ledger:live:BTCUSDT:15m"

#: A retention bound small enough that the service window is genuinely
#: trimmed by the fixture history (the configuration-derived bound of the
#: shared fixture pipeline is 20, which the 18-candle fixture never exceeds).
TIGHT = RetentionPolicy(local_context_bound=3)

#: A pipeline whose quality threshold suppresses every publication, so the
#: Phase 26D ledger stays empty. Used only by the C4 empty-ledger test.
SILENT = configuration(threshold=40)


# -- shared offline plumbing ---------------------------------------------------


def build_service(
    cycle_windows: tuple[int, ...],
    *,
    store: MemoryLedgerStore | None = None,
    window_store: MemoryDatasetStore | None = None,
    checkpoint_store: Any = None,
    delivery: TelegramDeliveryIntegration | None = None,
    retention: RetentionPolicy | None = None,
    pipeline: BacktestConfiguration | None = None,
    env_over: dict[str, str] | None = None,
) -> LiveService:
    """Assemble a live service over cumulative scripted kline pages."""

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
        configuration=pipeline if pipeline is not None else configuration(),
        market_transport=transport,
        delivery=delivery,
        checkpoint_store=checkpoint_store,
        retention=retention,
    )


def offline_delivery() -> TelegramDeliveryIntegration:
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=True))
    return TelegramDeliveryIntegration(OfflinePayloadSink(), coordinator=coordinator)


def reference_frames(pipeline: BacktestConfiguration | None = None) -> tuple[SignalSnapshot, ...]:
    """The frozen offline chain's frames over the full 18-candle fixture."""

    if pipeline is None:
        return expected_frames(bars("15m", EXTENDED_PRICES, start=EIGHT))
    from smcsignal.analysis.backtest import ReplayDataset
    from smcsignal.series import series_frames

    dataset = ReplayDataset(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=bars("15m", EXTENDED_PRICES, start=EIGHT),
        higher_candles=higher_candles(),
        venue="binance_spot",
        provider="binance_public",
        dataset_id="live-feed:v1",
    )
    return series_frames(dataset, pipeline)


def valid_checkpoint() -> LiveCheckpoint:
    """One real checkpoint produced by the real service for BTCUSDT/15m."""

    checkpoints = MemoryCheckpointStore()
    service = build_service((6, 9), checkpoint_store=checkpoints)
    service.run_cycle()
    checkpoint = checkpoints.load(CHECKPOINT_KEY)
    assert checkpoint is not None
    return checkpoint


def foreign_schema(checkpoint: LiveCheckpoint, version: object) -> LiveCheckpoint:
    """A checkpoint claiming a schema version its own constructor forbids.

    ``LiveCheckpoint.__post_init__`` rejects any version other than
    ``CHECKPOINT_SCHEMA_VERSION``, so the only way such a value can exist is
    for something that does not go through that constructor to produce it —
    precisely the situation the service-side gate exists for (a store or
    loader built for a different schema generation handing back its own
    object). The slots are copied verbatim and only ``schema_version`` is
    overridden, so every other field still matches the declaration.
    """

    impostor = object.__new__(LiveCheckpoint)
    for name in LiveCheckpoint.__slots__:
        object.__setattr__(impostor, name, getattr(checkpoint, name))
    object.__setattr__(impostor, "schema_version", version)
    return impostor


class PinnedCheckpointStore:
    """Store whose ``load`` always returns one pinned checkpoint.

    ``writable=False`` (the default) makes any write an assertion failure, so
    the rejection tests also prove a refused restore never reaches the store.
    """

    def __init__(self, checkpoint: LiveCheckpoint, *, writable: bool = False) -> None:
        self._checkpoint = checkpoint
        self._writable = writable
        self.load_calls: list[str] = []
        self.save_calls: list[str] = []

    def save(self, key: str, checkpoint: LiveCheckpoint) -> None:
        if not self._writable:
            raise AssertionError("a pinned store must never be written")
        self.save_calls.append(key)

    def load(self, key: str) -> LiveCheckpoint | None:
        self.load_calls.append(key)
        return self._checkpoint

    def contains(self, key: str) -> bool:
        return True


# -- C1: the schema-version gate must actually fire ----------------------------


@pytest.mark.parametrize(
    "version",
    [CHECKPOINT_SCHEMA_VERSION + 1, CHECKPOINT_SCHEMA_VERSION - 1, 0, 99, "1", None],
)
def test_service_refuses_a_checkpoint_declaring_another_schema_version(
    version: object,
) -> None:
    """C1 — a foreign declared schema version is rejected, whatever it is.

    The audited comparison (``schema_version != schema_version``) was
    tautological and returned ``False`` for every one of these values.
    """

    pinned = PinnedCheckpointStore(foreign_schema(valid_checkpoint(), version))
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    with pytest.raises(LiveCheckpointError, match=r"mismatched: schema_version\)"):
        build_service((9, 9), store=ledger, window_store=window, checkpoint_store=pinned)
    # Refused before any runtime, ledger, or window state was created.
    assert pinned.load_calls == [CHECKPOINT_KEY]
    assert ledger.load(LEDGER_KEY) is None
    assert window.load("window:live:BTCUSDT:15m") is None


def test_service_accepts_a_checkpoint_declaring_the_supported_schema_version() -> None:
    """C1 control — the gate discriminates; it is not an unconditional raise."""

    checkpoint = valid_checkpoint()
    assert checkpoint.schema_version == CHECKPOINT_SCHEMA_VERSION
    pinned = PinnedCheckpointStore(checkpoint, writable=True)
    service = build_service(
        (9, 9),
        store=MemoryLedgerStore(),
        window_store=MemoryDatasetStore(),
        checkpoint_store=pinned,
    )
    assert service.runtime.processed_count == checkpoint.frame_count
    # A fresh ledger store means a bootstrap open, but the regenerated history
    # is exactly the checkpoint's, frame for frame.
    assert service.session.frames_verified == checkpoint.frame_count
    # The startup re-persist went through, so the gate genuinely passed.
    assert pinned.save_calls == [CHECKPOINT_KEY]


def test_schema_drift_in_stored_bytes_is_refused_end_to_end(tmp_path: Path) -> None:
    """C1 — a checkpoint file written by another schema version fails closed.

    This is the production shape of the risk: a newer/older writer left a
    ``.checkpoint.json`` behind. The bytes are re-canonicalized with a valid
    digest so the failure can only come from the schema gate, never from the
    integrity check.
    """

    checkpoints = FileCheckpointStore(tmp_path)
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = build_service((6, 9), store=ledger, window_store=window, checkpoint_store=checkpoints)
    service.run_cycle()
    files = list(tmp_path.glob("*.checkpoint.json"))
    assert len(files) == 1
    document = json.loads(checkpoint_bytes(valid_checkpoint()))
    document["schema_version"] = CHECKPOINT_SCHEMA_VERSION + 1
    content = {key: value for key, value in document.items() if key != "content_digest"}
    document["content_digest"] = "checkpoint:" + digest(content)
    files[0].write_bytes(canonical_bytes(document))

    with pytest.raises(LiveCheckpointError, match="schema_version"):
        build_service((9, 9), store=ledger, window_store=window, checkpoint_store=checkpoints)


# -- T1: true rejection tests --------------------------------------------------


def test_checkpoint_from_another_symbol_is_refused() -> None:
    """T1 — an ETHUSDT checkpoint is never restored for a BTCUSDT service.

    ``LiveCheckpoint`` binds ``symbol`` to ``series.symbol``, so a symbol
    drift is necessarily reported as both ``symbol`` and ``series``; the
    assertion pins that exact report rather than a substring of it.
    """

    checkpoint = valid_checkpoint()
    impostor = replace(
        checkpoint,
        symbol="ETHUSDT",
        series=SeriesProvenance(
            "ETHUSDT",
            checkpoint.primary_timeframe,
            checkpoint.series.venue,
            checkpoint.series.provider,
            checkpoint.series.dataset_id,
        ),
    )
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    pinned = PinnedCheckpointStore(impostor)
    with pytest.raises(LiveCheckpointError, match=r"mismatched: symbol, series\); refusing"):
        build_service((9, 9), store=ledger, window_store=window, checkpoint_store=pinned)
    assert ledger.load(LEDGER_KEY) is None


def test_checkpoint_from_another_primary_timeframe_is_refused() -> None:
    """T1 — a 1h checkpoint is never restored for a 15m service."""

    checkpoint = valid_checkpoint()
    impostor = replace(
        checkpoint,
        primary_timeframe="1h",
        series=SeriesProvenance(
            checkpoint.symbol,
            "1h",
            checkpoint.series.venue,
            checkpoint.series.provider,
            checkpoint.series.dataset_id,
        ),
    )
    ledger = MemoryLedgerStore()
    pinned = PinnedCheckpointStore(impostor)
    with pytest.raises(
        LiveCheckpointError, match=r"mismatched: primary_timeframe, series\); refusing"
    ):
        build_service(
            (9, 9),
            store=ledger,
            window_store=MemoryDatasetStore(),
            checkpoint_store=pinned,
        )
    assert ledger.load(LEDGER_KEY) is None


def test_checkpoint_with_another_higher_timeframe_set_is_refused() -> None:
    """T1 — a 1h-only checkpoint is never restored for a 1h+4h declaration."""

    checkpoint = valid_checkpoint()
    assert checkpoint.higher_timeframes == ("1h", "4h")
    impostor = replace(
        checkpoint,
        higher_timeframes=("1h",),
        higher_candles={"1h": checkpoint.higher_candles["1h"]},
        last_higher={"1h": checkpoint.last_higher["1h"]},
    )
    ledger = MemoryLedgerStore()
    with pytest.raises(LiveCheckpointError, match=r"mismatched: higher_timeframes\); refusing"):
        build_service(
            (9, 9),
            store=ledger,
            window_store=MemoryDatasetStore(),
            checkpoint_store=PinnedCheckpointStore(impostor),
        )
    assert ledger.load(LEDGER_KEY) is None


def test_checkpoint_from_another_series_is_refused() -> None:
    """T1 — same symbol and timeframe, different venue: still refused."""

    checkpoint = valid_checkpoint()
    impostor = replace(
        checkpoint,
        series=SeriesProvenance(
            checkpoint.symbol,
            checkpoint.primary_timeframe,
            "binance_futures",
            checkpoint.series.provider,
            checkpoint.series.dataset_id,
        ),
    )
    ledger = MemoryLedgerStore()
    with pytest.raises(LiveCheckpointError, match=r"mismatched: series\); refusing"):
        build_service(
            (9, 9),
            store=ledger,
            window_store=MemoryDatasetStore(),
            checkpoint_store=PinnedCheckpointStore(impostor),
        )
    assert ledger.load(LEDGER_KEY) is None


def test_rejected_checkpoint_leaves_the_store_untouched() -> None:
    """T1 — a refusal is a read-only event for every durable store."""

    checkpoints = MemoryCheckpointStore()
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = build_service((6, 9), store=ledger, window_store=window, checkpoint_store=checkpoints)
    service.run_cycle()
    before_ledger = ledger.load(LEDGER_KEY)
    before_window = window.load(service.window_key)
    before_checkpoint = checkpoints.load(CHECKPOINT_KEY)
    assert before_ledger is not None and before_window is not None
    assert before_checkpoint is not None

    impostor = replace(
        before_checkpoint,
        series=SeriesProvenance(
            before_checkpoint.symbol,
            before_checkpoint.primary_timeframe,
            "some_other_venue",
            before_checkpoint.series.provider,
            before_checkpoint.series.dataset_id,
        ),
    )
    checkpoints.save(CHECKPOINT_KEY, impostor)
    with pytest.raises(LiveCheckpointError):
        build_service((9, 9), store=ledger, window_store=window, checkpoint_store=checkpoints)
    assert ledger.load(LEDGER_KEY) == before_ledger
    assert window.load(service.window_key) == before_window


# -- T2: restart equivalence as a sequence -------------------------------------


def one_frame_pages(start: int, stop: int) -> tuple[int, ...]:
    """Cumulative page sizes advancing exactly one candle per poll."""

    return tuple(range(start, stop + 1))


def collect_sequence(
    service: LiveService, cycles: int
) -> tuple[tuple[SignalSnapshot, ...], list[str]]:
    """Run ``cycles`` cycles; return the published frames and delivered ids."""

    frames: list[SignalSnapshot] = []
    for _ in range(cycles):
        report = service.run_cycle()
        assert report.frames == 1, "the sequence test needs exactly one frame per cycle"
        latest = service.runtime.latest
        assert latest is not None
        frames.append(latest)
    delivered = [payload.signal_id for payload in service.delivery.bridge.sink.seen]
    return tuple(frames), delivered


def test_restart_reproduces_the_uninterrupted_output_sequence_frame_by_frame() -> None:
    """T2 — 12 consecutive frames, compared one by one, not just the last.

    Run A never restarts. Run B checkpoints after frame 10, restarts, and
    continues. Both must publish the identical ordered frame sequence *and*
    the identical ordered delivery sequence.
    """

    reference = expected_frames(bars("15m", EXTENDED_PRICES, start=EIGHT))
    assert len(reference) == 18

    # Run A: uninterrupted, one candle per cycle.
    uninterrupted = build_service(
        one_frame_pages(6, 18),
        store=MemoryLedgerStore(),
        window_store=MemoryDatasetStore(),
        checkpoint_store=MemoryCheckpointStore(),
        delivery=offline_delivery(),
    )
    frames_a, delivered_a = collect_sequence(uninterrupted, 12)
    assert uninterrupted.runtime.processed_count == 18
    assert frames_a == reference[6:], "the uninterrupted live run must equal the frozen chain"

    # Run B: five cycles, checkpoint, restart, seven more cycles.
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    checkpoints = MemoryCheckpointStore()
    first_leg = build_service(
        one_frame_pages(6, 11),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        delivery=offline_delivery(),
    )
    frames_b1, delivered_b1 = collect_sequence(first_leg, 5)
    assert first_leg.runtime.processed_count == 11

    restarted = build_service(
        one_frame_pages(12, 18),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        delivery=offline_delivery(),
    )
    assert restarted.session.recovered_from is not None
    assert restarted.runtime.processed_count == 11
    frames_b2, delivered_b2 = collect_sequence(restarted, 7)
    assert restarted.runtime.processed_count == 18

    frames_b = frames_b1 + frames_b2
    delivered_b = delivered_b1 + delivered_b2

    # The whole ordered sequence, frame by frame.
    assert len(frames_b) == len(frames_a) == 12
    # Non-vacuity: the compared sequence is twelve *distinct* frames mixing
    # both statuses, so an off-by-one or a stale frame cannot slip through.
    assert len({frame.signal_id for frame in frames_a}) == 12
    assert {frame.status.name for frame in frames_a} == {"BUY_SIGNAL", "NO_SIGNAL"}
    for index, (expected, actual) in enumerate(zip(frames_a, frames_b, strict=True)):
        assert actual == expected, f"frame {index + 6} diverged after the restart"
        assert actual.signal_id == expected.signal_id
        assert actual.status is expected.status
    assert frames_b == reference[6:]
    # Deliveries diverge no more than the frames do.
    assert delivered_b == delivered_a
    assert len(delivered_a) == 3  # BUYs at indices 8, 12, 16 (index 4 is warm-up)


def test_repeated_restarts_reproduce_the_uninterrupted_output_sequence() -> None:
    """T2 — the equivalence holds across two restarts, not just one."""

    reference = expected_frames(bars("15m", EXTENDED_PRICES, start=EIGHT))

    uninterrupted = build_service(
        one_frame_pages(6, 18),
        store=MemoryLedgerStore(),
        window_store=MemoryDatasetStore(),
        checkpoint_store=MemoryCheckpointStore(),
        delivery=offline_delivery(),
    )
    frames_a, delivered_a = collect_sequence(uninterrupted, 12)

    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    checkpoints = MemoryCheckpointStore()
    collected: list[SignalSnapshot] = []
    delivered: list[str] = []
    # Three legs of four frames each, with a full restart between them. Leg 1
    # polls the warm-up page first; the later legs start at the first page
    # carrying a candle the restored cursor has not seen.
    legs = (one_frame_pages(6, 10), one_frame_pages(11, 14), one_frame_pages(15, 18))
    for pages in legs:
        service = build_service(
            pages,
            store=ledger,
            window_store=window,
            checkpoint_store=checkpoints,
            delivery=offline_delivery(),
        )
        frames, seen = collect_sequence(service, 4)
        collected.extend(frames)
        delivered.extend(seen)

    assert tuple(collected) == frames_a == reference[6:]
    assert delivered == delivered_a


def test_restart_is_exact_even_with_the_window_bounded_below_the_history() -> None:
    """T2 — with retention trimming the window, the checkpoint is the source.

    The service window is held to three candles while the history runs to
    eighteen, so nothing but the checkpoint can reproduce the sequence.
    """

    reference = expected_frames(bars("15m", EXTENDED_PRICES, start=EIGHT))

    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    checkpoints = MemoryCheckpointStore()
    first_leg = build_service(
        one_frame_pages(6, 11),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        retention=TIGHT,
        delivery=offline_delivery(),
    )
    frames_b1, _ = collect_sequence(first_leg, 5)
    assert len(first_leg.primary_window) == TIGHT.local_context_bound

    restarted = build_service(
        one_frame_pages(12, 18),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        retention=TIGHT,
        delivery=offline_delivery(),
    )
    frames_b2, _ = collect_sequence(restarted, 7)
    assert restarted.runtime.processed_count == 18

    uninterrupted = build_service(
        one_frame_pages(6, 18),
        store=MemoryLedgerStore(),
        window_store=MemoryDatasetStore(),
        checkpoint_store=MemoryCheckpointStore(),
        retention=TIGHT,
        delivery=offline_delivery(),
    )
    frames_a, _ = collect_sequence(uninterrupted, 12)

    assert frames_b1 + frames_b2 == frames_a == reference[6:]


# -- T3: checkpoint failure aborts before delivery -----------------------------


def test_checkpoint_persistence_failure_aborts_the_cycle_before_delivery() -> None:
    """T3 — ledger first, checkpoint fails, delivery never runs."""

    order: list[str] = []

    class RecordingLedger(MemoryLedgerStore):
        def save(self, key: str, snapshot: Any) -> None:
            order.append("ledger")
            super().save(key, snapshot)

    class RecordingWindow(MemoryDatasetStore):
        def save(self, key: str, dataset: Any) -> None:
            order.append("window")
            super().save(key, dataset)

    class FailingCheckpoints(MemoryCheckpointStore):
        def __init__(self) -> None:
            super().__init__()
            self.armed = False

        def save(self, key: str, checkpoint: Any) -> None:
            order.append("checkpoint")
            if self.armed:
                raise OSError("simulated checkpoint write failure")
            super().save(key, checkpoint)

    class RecordingDelivery(TelegramDeliveryIntegration):
        def deliver(self, frame: Any, destination_id: str) -> Any:
            order.append("delivery")
            return super().deliver(frame, destination_id)

    ledger = RecordingLedger()
    window = RecordingWindow()
    checkpoints = FailingCheckpoints()
    delivery = RecordingDelivery(
        OfflinePayloadSink(),
        coordinator=DeliveryCoordinator(None, config=OrchestrationConfig(enabled=True)),
    )
    service = build_service(
        (6, 9, 13),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        delivery=delivery,
    )
    startup_checkpoint = checkpoints.load(CHECKPOINT_KEY)
    assert startup_checkpoint is not None
    ledger_before = ledger.load(LEDGER_KEY)
    assert ledger_before is not None
    observations_before = len(ledger_before.observations)

    # The same pages with a healthy checkpoint store do owe a delivery, so a
    # missing "delivery" below is the abort and not an empty cycle.
    control = build_service((6, 9, 13), delivery=offline_delivery())
    assert control.run_cycle().buy_signals == 1

    checkpoints.armed = True
    order.clear()
    with pytest.raises(OSError, match="simulated checkpoint write failure"):
        service.run_cycle()

    # Ledger persistence completed, then the window, then the checkpoint
    # failed — and the delivery loop was never entered.
    assert order == ["ledger", "window", "checkpoint"]
    assert "delivery" not in order
    assert delivery.bridge.sink.seen == []
    assert service.cycles == 0

    # The ledger really is durable: the cycle's BUY observation is stored.
    ledger_after = ledger.load(LEDGER_KEY)
    assert ledger_after is not None
    assert len(ledger_after.observations) == observations_before + 1

    # The checkpoint is untouched — still the last successful one.
    assert checkpoints.load(CHECKPOINT_KEY) == startup_checkpoint


def test_cycle_recovers_after_a_transient_checkpoint_failure() -> None:
    """T3 follow-through — the abort is per-cycle, not terminal for the service."""

    class FlakyCheckpoints(MemoryCheckpointStore):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = False

        def save(self, key: str, checkpoint: Any) -> None:
            if self.fail_next:
                self.fail_next = False
                raise OSError("simulated transient checkpoint write failure")
            super().save(key, checkpoint)

    checkpoints = FlakyCheckpoints()
    service = build_service((6, 9, 13, 17), checkpoint_store=checkpoints)
    checkpoints.fail_next = True
    with pytest.raises(OSError):
        service.run_cycle()
    # The next cycle succeeds and the checkpoint catches up to the runtime.
    report = service.run_cycle()
    assert report.checkpoint_persisted is True
    stored = checkpoints.load(CHECKPOINT_KEY)
    assert stored is not None
    assert stored.frame_count == service.runtime.processed_count


# -- C4: the documented lost-checkpoint fallback -------------------------------


def test_lost_checkpoint_with_a_bounded_window_fails_closed(tmp_path: Path) -> None:
    """C4 — with ledger content, the Phase 26G verified open refuses to start.

    Exact path exercised: ``checkpoint_store.load`` returns ``None``, the
    identity block is skipped, the bounded Phase 27 window becomes the
    recovery source, and ``open_ledger_session`` cannot reproduce the stored
    snapshot from the truncated warm-up.
    """

    checkpoints = FileCheckpointStore(tmp_path)
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = build_service(
        (6, 9, 13, 17),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        retention=TIGHT,
    )
    for _ in range(3):
        service.run_cycle()
    assert service.runtime.processed_count == 17
    assert len(service.primary_window) == TIGHT.local_context_bound

    stored_ledger = ledger.load(LEDGER_KEY)
    assert stored_ledger is not None
    assert stored_ledger.observations, "this scenario needs a non-empty ledger"
    persisted_window = window.load(service.window_key)
    assert persisted_window is not None
    assert len(persisted_window.candles) < 17, "the persisted window really is truncated"

    files = list(tmp_path.glob("*.checkpoint.json"))
    assert len(files) == 1
    files[0].unlink()
    assert checkpoints.load(CHECKPOINT_KEY) is None

    with pytest.raises(AnalysisInputError, match="recovery verification failed"):
        build_service(
            (17, 17),
            store=ledger,
            window_store=window,
            checkpoint_store=checkpoints,
            retention=TIGHT,
        )
    # Fail closed means the durable ledger is byte-for-byte unchanged.
    assert ledger.load(LEDGER_KEY) == stored_ledger


def test_lost_checkpoint_with_an_empty_ledger_is_not_detected(tmp_path: Path) -> None:
    """C4 — the residual gap, pinned as a documented regression.

    With no published BUY the stored ledger snapshot is empty, so the frozen
    Phase 26G open authorizes on an empty-versus-empty comparison *before*
    replaying a frame and accepts the truncated warm-up. This test asserts
    the gap exists and is measurable; it does not endorse it. See the
    "Lost checkpoint with a bounded window" section of ``service.py``.
    """

    reference = reference_frames(SILENT)
    checkpoints = FileCheckpointStore(tmp_path)
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    service = build_service(
        (6, 9, 13, 17),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        retention=TIGHT,
        pipeline=SILENT,
    )
    for _ in range(3):
        service.run_cycle()
    stored_ledger = ledger.load(LEDGER_KEY)
    assert stored_ledger is not None
    assert stored_ledger.observations == ()
    assert stored_ledger.finalized == ()
    latest_before_restart = service.runtime.latest
    assert latest_before_restart == reference[16]

    list(tmp_path.glob("*.checkpoint.json"))[0].unlink()

    # No exception: the truncated warm-up is accepted.
    restarted = build_service(
        (17, 17),
        store=ledger,
        window_store=window,
        checkpoint_store=checkpoints,
        retention=TIGHT,
        pipeline=SILENT,
    )
    assert restarted.session.recovered_from is not None
    assert restarted.runtime.processed_count == 7, (
        "the fallback replayed only the persisted bounded window, not the 17-candle history"
    )
    assert restarted.runtime.processed_count != service.runtime.processed_count
    # And the divergence is real, not cosmetic: the same candle now carries a
    # different signal identity than it did in the uninterrupted run.
    assert restarted.runtime.latest != latest_before_restart
    assert restarted.runtime.latest is not None
    assert restarted.runtime.latest.signal_id != reference[16].signal_id
