"""Phase 33 live service tests: feed → runtime → ledger + Telegram, offline only.

Delivery runs over the existing Phase 24 primitives: the real ``TelegramSink``
is driven by a scripted fake HTTP transport (never the network), and dry-run
uses the existing ``OfflinePayloadSink``. The pytest process forbids socket
access, so no test can reach Telegram even by accident.
"""

from __future__ import annotations

import time

from smcsignal.analysis.backtest import ReplayDataset
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus
from smcsignal.datasets import DatasetStore, MemoryDatasetStore
from smcsignal.delivery.models import DeliveryState
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.telegram.config import TelegramConfig
from smcsignal.delivery.telegram.integration import TelegramDeliveryIntegration
from smcsignal.delivery.telegram.sink import TelegramSink
from smcsignal.delivery.transport import OfflinePayloadSink
from smcsignal.live import DESTINATION_ID, LiveService, load_live_config, start_live_service
from smcsignal.persistence import LedgerStore, MemoryLedgerStore
from smcsignal.series import series_frames
from tests.backtest.helpers import EIGHT, PRIMARY_PRICES, bars, configuration, higher_candles
from tests.delivery.telegram._helpers import FakeHttpTransport
from tests.live.test_market_feed import ScriptedTransport

STEP_MS = 15 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
EXTENDED_PRICES = (*PRIMARY_PRICES, 37)  # one extra candle for the restart test


def kline_from(candle: object, step_ms: int) -> list[object]:
    """One 12-field kline row carrying a fixture candle's exact OHLCV values."""

    from smcsignal.data.models import OHLCV

    assert isinstance(candle, OHLCV)
    opening = int(candle.timestamp.timestamp() * 1000)
    return [
        opening,
        str(candle.open),
        str(candle.high),
        str(candle.low),
        str(candle.close),
        str(candle.volume),
        opening + step_ms - 1,
        "0",
        "0",
        "0",
        "0",
        "0",
    ]


def higher_payload(timeframe: str) -> list[list[object]]:
    """One cumulative kline page for a fixture higher timeframe (repeats)."""

    step = HOUR_MS if timeframe == "1h" else 4 * HOUR_MS
    return [kline_from(candle, step) for candle in higher_candles()[timeframe]]


def primary_candles(count: int) -> tuple:
    return bars("15m", EXTENDED_PRICES[:count], start=EIGHT)


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


def expected_frames(candles: tuple) -> tuple[SignalSnapshot, ...]:
    return series_frames(live_dataset(candles), configuration())


def buy_ids(frames: tuple[SignalSnapshot, ...]) -> list[str]:
    return [frame.signal_id for frame in frames if frame.status is SignalStatus.BUY_SIGNAL]


def minute_payload(count: int) -> list[list[object]]:
    """One cumulative 15m kline page covering the first ``count`` fixtures."""

    return [kline_from(candle, STEP_MS) for candle in primary_candles(count)]


def scripted_service(
    cycle_windows: tuple[int, ...],
    *,
    env_over: dict[str, str] | None = None,
    store: LedgerStore | None = None,
    window_store: DatasetStore | None = None,
    delivery: TelegramDeliveryIntegration | None = None,
) -> LiveService:
    """Build a live service over cumulative scripted kline pages.

    ``cycle_windows`` are cumulative primary-candle counts: entry ``n`` is the
    page returned by poll ``n`` (poll 0 is the startup warm-up poll whenever
    no window is restored; with a restored window, poll 0 is the first cycle).
    """

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
    )


def test_warmup_records_history_without_delivering_it() -> None:
    # Warm-up covers candles 0..5 (BUY at index 4): recorded in the ledger,
    # never delivered — history replay is not a broadcast.
    service = scripted_service((6, 6))
    snapshot = service.session.store.load(service.ledger_key)
    assert snapshot is not None
    assert len(snapshot.observations) == 1  # the warm-up BUY at index 4
    assert isinstance(service.delivery.bridge.sink, OfflinePayloadSink)
    assert service.delivery.bridge.sink.seen == []  # nothing was delivered at startup
    assert service.session.recovered_from is None  # fresh bootstrap


def test_cycle_frames_match_the_frozen_chain_and_reach_the_ledger() -> None:
    expected = expected_frames(primary_candles(17))
    service = scripted_service((6, 9, 13, 17, 17))
    first = service.run_cycle()
    assert first.frames == 3
    assert first.buy_signals == 1  # BUY at index 8
    assert first.ledger_persisted is True
    second = service.run_cycle()
    assert second.frames == 4
    assert second.buy_signals == 1  # BUY at index 12
    third = service.run_cycle()
    assert third.frames == 4
    assert third.buy_signals == 1  # BUY at index 16
    assert service.runtime.processed_count == 17
    snapshot = service.session.store.load(service.ledger_key)
    assert snapshot is not None
    assert len(snapshot.observations) == 4  # all four fixture BUY facts
    delivered_ids = [payload.signal_id for payload in service.delivery.bridge.sink.seen]
    assert delivered_ids == buy_ids(expected)[1:]  # the warm-up BUY was not broadcast


def test_buy_frames_reach_delivery_and_non_buy_frames_do_not() -> None:
    expected = expected_frames(primary_candles(17))
    service = scripted_service((6, 9, 13, 17, 17))
    reports = [service.run_cycle() for _ in range(3)]
    delivered = [state for report in reports for state in report.delivery_states]
    assert delivered == [DeliveryState.DELIVERED] * 3  # offline sink: would-send
    seen = service.delivery.bridge.sink.seen
    assert len(seen) == 3
    non_buy_ids = [
        frame.signal_id for frame in expected if frame.status is not SignalStatus.BUY_SIGNAL
    ]
    for payload in seen:
        assert payload.signal_id in buy_ids(expected)
        assert payload.signal_id not in non_buy_ids
        assert payload.destination_id == DESTINATION_ID


def test_dry_run_never_constructs_a_telegram_transport() -> None:
    service = scripted_service((6, 9, 13, 17))
    assert isinstance(service.delivery.bridge.sink, OfflinePayloadSink)
    assert not isinstance(service.delivery.bridge.sink, TelegramSink)
    service.run_cycle()
    assert len(service.delivery.bridge.sink.seen) == 1


def test_disabled_service_never_attempts_delivery() -> None:
    service = scripted_service((6, 9), env_over={"LIVE_ENABLED": "false"})
    report = service.run_cycle()
    assert report.frames == 3
    assert report.buy_signals == 1
    assert report.delivery_states == (DeliveryState.NOT_ATTEMPTED,)
    assert service.delivery.bridge.sink.seen == []


def real_delivery(http: FakeHttpTransport) -> TelegramDeliveryIntegration:
    sink = TelegramSink(
        "123456:TEST-TOKEN",
        config=TelegramConfig(enabled=True, retry_max_attempts=2),
        destinations={DESTINATION_ID: "-1001234567890"},
        http_transport=http,
        sleep_fn=lambda seconds: None,
        now_fn=time.monotonic,
    )
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=True))
    return TelegramDeliveryIntegration(sink, coordinator=coordinator)


def test_telegram_failures_are_reported_and_delivery_recovers() -> None:
    # Budget: 2 sink attempts per delivery. Scripted outcomes: deliveries 1 and
    # 2 exhaust the budget on HTTP 500s; delivery 3 succeeds on its last try.
    http = FakeHttpTransport(
        [
            (500, '{"ok": false}'),
            (500, '{"ok": false}'),
            (500, '{"ok": false}'),
            (500, '{"ok": false}'),
            None,
        ]
    )
    delivery = real_delivery(http)
    service = scripted_service(
        (6, 9, 13, 17),
        env_over={
            "LIVE_DRY_RUN": "false",
            "TELEGRAM_BOT_TOKEN": "123456:TEST-TOKEN",
            "TELEGRAM_CHAT_ID": "-1001234567890",
        },
        delivery=delivery,
    )
    assert service.run_cycle().delivery_states == (DeliveryState.FAILED,)
    assert service.run_cycle().delivery_states == (DeliveryState.FAILED,)
    assert service.run_cycle().delivery_states == (DeliveryState.DELIVERED,)
    # Delivery failures never touch the ledger: all four BUY facts stay recorded.
    snapshot = service.session.store.load(service.ledger_key)
    assert snapshot is not None
    assert len(snapshot.observations) == 4
    counters = delivery.counters(DESTINATION_ID)
    assert counters.failed == 2
    assert counters.delivered_total == 1


def test_repeated_polls_and_the_registry_prevent_duplicate_delivery() -> None:
    service = scripted_service((6, 9, 9, 9))
    first = service.run_cycle()
    assert first.buy_signals == 1
    payload_count = len(service.delivery.bridge.sink.seen)
    repeated = service.run_cycle()  # identical page: everything behind the cursor
    assert repeated.frames == 0
    assert repeated.buy_signals == 0
    assert repeated.delivery_states == ()
    assert len(service.delivery.bridge.sink.seen) == payload_count
    # The existing registry also blocks an identical redelivery of one frame:
    # the cycle-1 BUY is already in the registry, so a second delivery attempt
    # is skipped by the frozen dedup primitive without reaching the sink.
    frame = service.runtime.latest
    assert frame is not None and frame.status is SignalStatus.BUY_SIGNAL
    redelivered = service.delivery.deliver(frame, DESTINATION_ID)
    assert redelivered.state is DeliveryState.SKIPPED_DUPLICATE
    assert len(service.delivery.bridge.sink.seen) == payload_count


def test_restart_restores_the_window_and_continues_deterministically() -> None:
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    service = scripted_service((6, 9, 13, 17), store=store, window_store=window_store)
    for _ in range(3):
        service.run_cycle()

    # A restart with the same stores: the window comes back from the Phase 27
    # dataset store, the ledger opens through verified Phase 26G recovery.
    restarted = scripted_service(
        (17, 18),
        store=store,
        window_store=window_store,
        delivery=service.delivery,
    )
    assert restarted.session.recovered_from is not None
    warm = restarted.run_cycle()  # the overlap page: nothing new
    assert warm.frames == 0
    continuation = restarted.run_cycle()  # one genuinely new closed candle
    assert continuation.frames == 1
    assert continuation.buy_signals == 0

    extended = expected_frames(primary_candles(18))
    assert restarted.runtime.latest == extended[17]
    assert restarted.runtime.processed_count == 18
    snapshot = restarted.session.store.load(restarted.ledger_key)
    assert snapshot is not None
    assert len(snapshot.observations) == 4  # the continuation published no BUY
