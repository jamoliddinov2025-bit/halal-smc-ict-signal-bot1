"""Phase 35D integration tests: durable outbox wiring over the frozen stack.

Real delivery runs through the frozen transport driven by a scripted offline
HTTP double (the pytest process forbids sockets, so the network is never
reachable). Market data is scripted through the existing live-test harness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smcsignal.analysis.signal_engine.models import SignalStatus
from smcsignal.datasets import MemoryDatasetStore
from smcsignal.delivery.models import DeliveryState
from smcsignal.delivery.outbox.models import OutboxConfig, OutboxState
from smcsignal.delivery.outbox.reconcile import expected_delivery_id
from smcsignal.delivery.outbox.sink import OutboxPayloadSink
from smcsignal.delivery.outbox.store import RECORD_SUFFIX, FileOutboxStore, key_digest
from smcsignal.delivery.transport import OfflinePayloadSink
from smcsignal.live import (
    LiveConfigurationError,
    LiveFeedError,
    RateLimitedFeedError,
    RetryAfterAwareLivePollLoop,
    load_live_config,
)
from smcsignal.live.outbox_wiring import (
    OutboxDrainingRunner,
    OutboxLiveDeployment,
    OutboxSettings,
    build_outbox_telegram_delivery,
    load_outbox_settings,
    start_outbox_gap_aware_live_service,
)
from smcsignal.live.poll_loop import CycleRunner, LivePollLoopConfig
from smcsignal.live.service import CycleReport
from smcsignal.persistence import MemoryLedgerStore
from tests.backtest.helpers import configuration
from tests.delivery.outbox.helpers import DESTINATION
from tests.delivery.telegram._helpers import FakeHttpTransport
from tests.live.test_market_feed import ScriptedTransport
from tests.live.test_service import (
    expected_frames,
    higher_payload,
    minute_payload,
    primary_candles,
)

REAL_ENV = {
    "LIVE_SYMBOL": "BTCUSDT",
    "LIVE_TIMEFRAME": "15m",
    "LIVE_ENABLED": "true",
    "LIVE_DRY_RUN": "false",
    "TELEGRAM_BOT_TOKEN": "123456:TEST-TOKEN",
    "TELEGRAM_CHAT_ID": "-1001234567890",
}


def market_transport(cycle_windows: tuple[int, ...]) -> ScriptedTransport:
    return ScriptedTransport(
        {
            "15m": [minute_payload(count) for count in cycle_windows],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )


def settings(root: Path, **config_over: object) -> OutboxSettings:
    return OutboxSettings(
        root=str(root / "outbox"), config=OutboxConfig(cooldown_seconds=0.0, **config_over)
    )


ACTIVATION = datetime(2024, 1, 1, 9, 30, tzinfo=UTC)


def seed_activation(tmp_path: Path, when: datetime = ACTIVATION) -> None:
    """Simulate an outbox activated before the cycle BUY candle opened.

    The fixture BUY candles open at 09:00 (warm-up) and 10:00 (first cycle);
    an activation at 09:30 keeps the warm-up signal historical while making
    the cycle signal eligible for reconciliation — exactly the production
    ordering of a service activated between two candles.
    """
    FileOutboxStore(tmp_path / "outbox").ensure_meta(when)


def start_deployment(
    tmp_path: Path,
    cycle_windows: tuple[int, ...],
    http: FakeHttpTransport,
    *,
    ledger_store: MemoryLedgerStore | None = None,
    window_store: MemoryDatasetStore | None = None,
    outbox_settings: OutboxSettings | None = None,
    env_over: dict[str, str] | None = None,
    clock: datetime = datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
) -> OutboxLiveDeployment:
    env = dict(REAL_ENV)
    env.update(env_over or {})
    resolved_settings = outbox_settings if outbox_settings is not None else settings(tmp_path)
    return start_outbox_gap_aware_live_service(
        load_live_config(env),
        store=ledger_store if ledger_store is not None else MemoryLedgerStore(),
        window_store=window_store if window_store is not None else MemoryDatasetStore(),
        settings=resolved_settings,
        configuration=configuration(),
        market_transport=market_transport(cycle_windows),
        transport=http,
        sleep_fn=lambda seconds: None,
        clock=lambda: clock,
    )


# -- settings ------------------------------------------------------------------------


def test_load_outbox_settings_defaults_and_validation() -> None:
    resolved = load_outbox_settings({"LIVE_OUTBOX_DIR": "data/outbox"})
    assert resolved.root == "data/outbox"
    assert resolved.config.max_attempts == 5
    assert resolved.config.cooldown_seconds == 60.0
    assert resolved.config.max_per_drain == 8
    with pytest.raises(LiveConfigurationError):
        load_outbox_settings({})
    with pytest.raises(LiveConfigurationError):
        load_outbox_settings({"LIVE_OUTBOX_DIR": "d", "LIVE_OUTBOX_MAX_ATTEMPTS": "11"})
    with pytest.raises(LiveConfigurationError):
        load_outbox_settings({"LIVE_OUTBOX_DIR": "d", "LIVE_OUTBOX_MAX_ATTEMPTS": "0"})
    with pytest.raises(LiveConfigurationError):
        load_outbox_settings({"LIVE_OUTBOX_DIR": "d", "LIVE_OUTBOX_COOLDOWN_SECONDS": "-1"})
    with pytest.raises(LiveConfigurationError):
        load_outbox_settings({"LIVE_OUTBOX_DIR": "d", "LIVE_OUTBOX_MAX_PER_DRAIN": "0"})
    custom = load_outbox_settings(
        {
            "LIVE_OUTBOX_DIR": "d",
            "LIVE_OUTBOX_MAX_ATTEMPTS": "7",
            "LIVE_OUTBOX_COOLDOWN_SECONDS": "5.5",
            "LIVE_OUTBOX_MAX_PER_DRAIN": "3",
        }
    )
    assert custom.config.max_attempts == 7
    assert custom.config.cooldown_seconds == 5.5
    assert custom.config.max_per_drain == 3


# -- dry-run / disabled: zero durable state -------------------------------------------


@pytest.mark.parametrize("env_over", [{"LIVE_DRY_RUN": "true"}, {"LIVE_ENABLED": "false"}])
def test_dry_run_and_disabled_create_zero_outbox_state(tmp_path: Path, env_over) -> None:
    root = tmp_path / "outbox"
    env = dict(REAL_ENV)
    env.update(env_over)
    config = load_live_config(env)
    resolved_settings = OutboxSettings(root=str(root), config=OutboxConfig())
    integration, sink = build_outbox_telegram_delivery(config, resolved_settings)
    assert sink is None
    assert not root.exists()  # not even a directory
    if env_over.get("LIVE_DRY_RUN") == "true":
        assert isinstance(integration.bridge.sink, OfflinePayloadSink)

    deployment = start_deployment(
        tmp_path, (6, 9), FakeHttpTransport(), env_over=env_over, outbox_settings=resolved_settings
    )
    assert deployment.outbox_sink is None
    assert deployment.runner is deployment.service  # bare service, no drain wrapper
    report = deployment.runner.run_cycle()
    assert report.frames == 3
    assert report.buy_signals == 1
    if env_over.get("LIVE_ENABLED") == "false":
        assert report.delivery_states == (DeliveryState.NOT_ATTEMPTED,)
    assert not root.exists()
    assert deployment.reconciliation_created == ()
    assert deployment.reconciliation_disabled_reason is None


def test_real_mode_requires_outbox_settings(tmp_path: Path) -> None:
    config = load_live_config(REAL_ENV)
    with pytest.raises(LiveConfigurationError):
        build_outbox_telegram_delivery(config, None)


# -- real-mode delivery through the durable outbox ------------------------------------


def test_real_cycle_failed_then_drained_to_delivered(tmp_path: Path) -> None:
    # One outbox attempt burns the frozen sink's three internal tries on 500s;
    # the same-cycle drain attempt then succeeds.
    seed_activation(tmp_path)
    http = FakeHttpTransport([(500, '{"ok": false}')] * 3 + [None])
    deployment = start_deployment(tmp_path, (6, 9, 9), http)
    assert isinstance(deployment.outbox_sink, OutboxPayloadSink)
    assert isinstance(deployment.runner, OutboxDrainingRunner)
    # Activation marker bootstrap: historical warm-up signals are excluded.
    assert deployment.reconciliation_created == ()
    assert deployment.reconciliation_disabled_reason is None

    report = deployment.runner.run_cycle()
    # The cycle report carries the inline receipt (FAILED); the same-call drain
    # pass then healed the delivery to DELIVERED.
    assert report.delivery_states == (DeliveryState.FAILED,)

    frames = expected_frames(primary_candles(9))
    buy_frames = [f for f in frames if f.status is SignalStatus.BUY_SIGNAL]
    cycle_buy = buy_frames[1]  # the BUY at candle index 8 (delivered in-cycle)
    delivery_id = expected_delivery_id(cycle_buy, DESTINATION)
    record = deployment.outbox_sink.store.load_record(delivery_id)
    assert record is not None
    assert record.state is OutboxState.DELIVERED
    assert record.attempts_used == 2  # failed attempt + drain retry
    assert record.possibly_duplicated is False
    # The warm-up BUY (index 4) has no record: history is never broadcast.
    warmup_buy = buy_frames[0]
    assert (
        deployment.outbox_sink.store.load_record(expected_delivery_id(warmup_buy, DESTINATION))
        is None
    )
    # Deterministic identity across paths: the record key is the frozen id.
    assert record.delivery_id == delivery_id


def test_restart_continues_budget_without_redelivery(tmp_path: Path) -> None:
    seed_activation(tmp_path)
    http = FakeHttpTransport([(500, '{"ok": false}')] * 3 + [None])
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    outbox_settings = settings(tmp_path)
    deployment = start_deployment(
        tmp_path,
        (6, 9, 9),
        http,
        ledger_store=ledger,
        window_store=window,
        outbox_settings=outbox_settings,
    )
    deployment.runner.run_cycle()
    store = deployment.outbox_sink.store
    record = store.all_records()[0]
    assert record.state is OutboxState.DELIVERED

    # Restart: same durable stores, fresh feed pages with nothing new.
    restart_http = FakeHttpTransport()
    restarted = start_deployment(
        tmp_path,
        (9, 9),
        restart_http,
        ledger_store=ledger,
        window_store=window,
        outbox_settings=outbox_settings,
    )
    assert restarted.reconciliation_created == ()  # terminal record respected
    assert restarted.reconciliation_disabled_reason is None
    report = restarted.runner.run_cycle()
    assert report.frames == 0
    assert report.delivery_states == ()
    assert restarted.outbox_sink.drain(datetime(2026, 9, 22, 13, 0, tzinfo=UTC)) == 0
    final = restarted.outbox_sink.store.load_record(record.delivery_id)
    assert final is not None and final.state is OutboxState.DELIVERED
    assert final.attempts_used == record.attempts_used
    assert restart_http.calls == []  # nothing was re-sent after the restart


def test_restart_reconciliation_recreates_missing_intent(tmp_path: Path) -> None:
    """Crash-window closure: window durable, record gone ⇒ intent recreated."""
    seed_activation(tmp_path)
    http = FakeHttpTransport([(500, '{"ok": false}')] * 3 + [None])
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    outbox_settings = settings(tmp_path)
    deployment = start_deployment(
        tmp_path,
        (6, 9, 9),
        http,
        ledger_store=ledger,
        window_store=window,
        outbox_settings=outbox_settings,
    )
    deployment.runner.run_cycle()
    store = deployment.outbox_sink.store
    record = store.all_records()[0]
    delivery_id = record.delivery_id
    # Lose the durable record (window and ledger survive).
    record_path = tmp_path / "outbox" / "records" / (key_digest(delivery_id) + RECORD_SUFFIX)
    record_path.unlink()

    restarted = start_deployment(
        tmp_path,
        (9, 9),
        FakeHttpTransport(),
        ledger_store=ledger,
        window_store=window,
        outbox_settings=outbox_settings,
    )
    assert restarted.reconciliation_created == (delivery_id,)
    recreated = restarted.outbox_sink.store.load_record(delivery_id)
    assert recreated is not None and recreated.state is OutboxState.QUEUED
    assert recreated.attempts_used == 0
    # The drain delivers the recreated intent; exactly one new send occurs.
    report = restarted.runner.run_cycle()
    assert report.frames == 0
    final = restarted.outbox_sink.store.load_record(delivery_id)
    assert final is not None and final.state is OutboxState.DELIVERED
    assert final.attempts_used == 1


def test_corrupt_activation_marker_fails_reconciliation_closed(tmp_path: Path) -> None:
    seed_activation(tmp_path)
    ledger = MemoryLedgerStore()
    window = MemoryDatasetStore()
    outbox_settings = settings(tmp_path)
    deployment = start_deployment(
        tmp_path,
        (6, 9, 9),
        FakeHttpTransport(),
        ledger_store=ledger,
        window_store=window,
        outbox_settings=outbox_settings,
    )
    deployment.runner.run_cycle()
    # Corrupt the activation marker, then restart.
    (tmp_path / "outbox" / "meta.outbox.json").write_text("{broken", encoding="utf-8")
    restarted = start_deployment(
        tmp_path,
        (9, 9),
        FakeHttpTransport(),
        ledger_store=ledger,
        window_store=window,
        outbox_settings=outbox_settings,
    )
    assert restarted.reconciliation_disabled_reason is not None
    assert restarted.reconciliation_created == ()
    # Existing records still recover; nothing is re-broadcast from history.
    assert restarted.outbox_sink.store.all_records()[0].state is OutboxState.DELIVERED


# -- draining runner semantics ---------------------------------------------------------


class StubRunner:
    def __init__(self, outcome: CycleReport | Exception) -> None:
        self.outcome = outcome
        self.calls = 0

    def run_cycle(self) -> CycleReport:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class SpySink(OutboxPayloadSink):
    def __init__(self, store: FileOutboxStore) -> None:
        super().__init__(store, _NullInner(), config=OutboxConfig(cooldown_seconds=0.0))
        self.drains: list[datetime] = []
        self.raise_on_drain: Exception | None = None

    def drain(self, now: datetime | None = None) -> int:
        self.drains.append(now if now is not None else datetime.now(UTC))
        if self.raise_on_drain is not None:
            raise self.raise_on_drain
        return 0


class _NullInner:
    def deliver_payload(self, payload):  # pragma: no cover - never called
        raise AssertionError("the spy sink never delegates")


def _report() -> CycleReport:
    return CycleReport(
        primary_candles=1,
        higher_candles=0,
        frames=1,
        buy_signals=0,
        delivery_states=(),
        ledger_persisted=True,
    )


def test_runner_delegates_report_and_drains_once(tmp_path: Path) -> None:
    sink = SpySink(FileOutboxStore(tmp_path / "outbox"))
    stub = StubRunner(_report())
    runner = OutboxDrainingRunner(stub, sink)
    report = runner.run_cycle()
    assert report is stub.outcome
    assert stub.calls == 1
    assert len(sink.drains) == 1


def test_runner_drains_after_cycle_failure_and_reraises(tmp_path: Path) -> None:
    sink = SpySink(FileOutboxStore(tmp_path / "outbox"))
    failure = LiveFeedError("feed down")
    runner = OutboxDrainingRunner(StubRunner(failure), sink)
    with pytest.raises(LiveFeedError):
        runner.run_cycle()
    assert len(sink.drains) == 1  # delivery health is independent of feed health


def test_runner_drain_failure_propagates_on_success(tmp_path: Path) -> None:
    sink = SpySink(FileOutboxStore(tmp_path / "outbox"))
    sink.raise_on_drain = RuntimeError("store vanished")
    runner = OutboxDrainingRunner(StubRunner(_report()), sink)
    with pytest.raises(RuntimeError, match="store vanished"):
        runner.run_cycle()


def test_runner_cycle_failure_takes_precedence_over_drain_failure(tmp_path: Path) -> None:
    sink = SpySink(FileOutboxStore(tmp_path / "outbox"))
    sink.raise_on_drain = RuntimeError("drain broke")
    failure = LiveFeedError("feed down")
    runner = OutboxDrainingRunner(StubRunner(failure), sink)
    with pytest.raises(LiveFeedError):
        runner.run_cycle()


def test_runner_validation(tmp_path: Path) -> None:
    sink = SpySink(FileOutboxStore(tmp_path / "outbox"))
    with pytest.raises(LiveConfigurationError):
        OutboxDrainingRunner(object(), sink)  # type: ignore[arg-type]
    with pytest.raises(LiveConfigurationError):
        OutboxDrainingRunner(StubRunner(_report()), None)  # type: ignore[arg-type]


# -- Retry-After composition -------------------------------------------------------------


class WallClock:
    def __init__(self) -> None:
        self.now_value = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now_value = self.now_value + timedelta(seconds=seconds)


def test_retry_after_loop_composes_with_draining_runner(tmp_path: Path) -> None:
    """The Phase 35C scheduler stays the only authority; the outbox piggybacks.

    Every recoverable cycle raises a rate-limit feed error carrying a
    server-directed 7-second Retry-After; the composed loop must back off at
    least that long while the drain still runs once per cycle.
    """
    sink = SpySink(FileOutboxStore(tmp_path / "outbox"))
    rate_limited = RateLimitedFeedError(status_code=429, retry_after="7", retry_after_seconds=7.0)
    runner = OutboxDrainingRunner(StubRunner(rate_limited), sink)
    clock = WallClock()
    loop = RetryAfterAwareLivePollLoop(
        runner,
        LivePollLoopConfig(timeframe="1m", wait_chunk_seconds=1.0),
        clock=clock,
        sleep_fn=clock.sleep,
        random_source=lambda: 0.0,
    )
    executed = loop.run(max_cycles=2)
    assert executed == 2
    assert len(sink.drains) == 2  # drain piggybacked every cycle, no second scheduler
    history = loop.history
    assert all(result.outcome.value == "recoverable_failure" for result in history)
    first = history[0]
    assert first.next_poll_at is not None
    delay = (first.next_poll_at - first.finished_at).total_seconds()
    assert delay >= 7.0  # Retry-After honored through the composition
    assert loop.consecutive_failures == 2


def test_poll_loop_accepts_draining_runner_as_cycle_runner(tmp_path: Path) -> None:
    from smcsignal.live.poll_loop import LivePollLoop

    sink = SpySink(FileOutboxStore(tmp_path / "outbox"))
    runner = OutboxDrainingRunner(StubRunner(_report()), sink)
    assert isinstance(runner, CycleRunner)
    clock = WallClock()
    loop = LivePollLoop(
        runner,
        LivePollLoopConfig(timeframe="1m", wait_chunk_seconds=1.0),
        clock=clock,
        sleep_fn=clock.sleep,
        random_source=lambda: 0.0,
    )
    executed = loop.run(max_cycles=1)
    assert executed == 1
    assert len(sink.drains) == 1
    assert loop.last_result is not None
    assert loop.last_result.outcome.value == "success"
