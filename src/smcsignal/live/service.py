"""Phase 33 live service: the smallest coordinator from feed to ledger and Telegram.

The service composes existing public seams and adds no engine of its own:

    LiveMarketFeed.poll()
        → LiveRuntime.update(candle)         (frozen Phase 3-17 chain)
            → SignalSnapshot
                ├→ open_ledger_session / LedgerSession.update / persist
                └→ TelegramDeliveryIntegration.deliver(frame, destination_id)

Every downstream capability is the frozen one: outcome observation runs
through the existing Phase 26B/26G ``LedgerSession`` over the caller's Phase
26D ``LedgerStore`` (a live ``SignalSnapshot`` is a sanctioned frame origin —
the 26G seam explicitly accepts frames "produced by any other origin");
delivery runs through the existing Phase 24 ``TelegramDeliveryIntegration``,
which owns projection, rendering, identity, deduplication, retry, receipts,
and counters. The service only decides *when* to call them, and only a frame
whose frozen status is ``BUY_SIGNAL`` is ever handed to delivery — the same
``require_buy_signal`` boundary the coordinator enforces internally. No
second threshold, halal rule, renderer, retry loop, or Telegram client exists
here, and nothing here can generate, gate, or veto a signal.

Restart support
---------------

The live-specific candle window (primary plus higher-timeframe closed
candles) persists through the existing Phase 27 ``DatasetStore`` as an exact
``ReplayDataset`` under a live key — no second persistence format. Startup
restores that window, deterministically reconstructs the analysis state, and
continues with new completed candles; ledger recovery stays the frozen
Phase 26G verified open over the regenerated warm-up frames.

Dry run
-------

``LIVE_DRY_RUN`` never constructs a real transport: the delivery integration
is built over the existing ``OfflinePayloadSink``, so a dry-run cycle records
would-be sends without any possibility of contacting Telegram. A disabled
service keeps the frozen orchestration master switch off (``NOT_ATTEMPTED``).

The service is a delivery-layer coordinator. It performs no trading, order,
position, sizing, broker, or execution action of any kind.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from smcsignal.analysis.backtest import BacktestConfiguration, ReplayDataset
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus
from smcsignal.data.binance import JsonTransport
from smcsignal.data.models import OHLCV
from smcsignal.datasets import DatasetStore
from smcsignal.delivery.models import DeliveryState
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.telegram.config import TelegramConfig
from smcsignal.delivery.telegram.http import HttpTransport
from smcsignal.delivery.telegram.integration import TelegramDeliveryIntegration
from smcsignal.delivery.telegram.sink import TelegramSink
from smcsignal.delivery.transport import OfflinePayloadSink, PayloadSink
from smcsignal.live.config import LiveConfigurationError, LiveServiceConfig
from smcsignal.live.market_feed import LiveMarketFeed
from smcsignal.live.runtime import LiveRuntime
from smcsignal.persistence import LedgerStore
from smcsignal.sessions import LedgerSession, open_ledger_session

DESTINATION_ID = "primary"
LIVE_VENUE = "binance_spot"
LIVE_DATASET_ID = "live-feed:v1"


def _default_sleep(seconds: float) -> None:
    """Stdlib backoff used when the caller injects no sleep boundary."""

    time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class CycleReport:
    """One poll cycle's deterministic outcome summary (pure reporting)."""

    primary_candles: int
    higher_candles: int
    frames: int
    buy_signals: int
    delivery_states: tuple[DeliveryState, ...]
    ledger_persisted: bool


class LiveService:
    """Run one poll cycle: new closed candles in, ledger and delivery out.

    The service owns the live candle window (the exact primary and
    higher-timeframe closed candles its runtime state was built from) and the
    per-timeframe dedup cursors; the runtime owns analysis state; the ledger
    session and delivery integration stay the frozen downstream seams.
    """

    def __init__(
        self,
        *,
        feed: LiveMarketFeed,
        runtime: LiveRuntime,
        session: LedgerSession,
        delivery: TelegramDeliveryIntegration,
        window_store: DatasetStore,
        window_key: str,
        primary_window: Sequence[OHLCV],
        higher_window: Mapping[str, Sequence[OHLCV]],
        destination_id: str = DESTINATION_ID,
    ) -> None:
        if not isinstance(feed, LiveMarketFeed):
            raise AnalysisInputError("live service requires a LiveMarketFeed")
        if not isinstance(runtime, LiveRuntime):
            raise AnalysisInputError("live service requires a LiveRuntime")
        if not isinstance(session, LedgerSession):
            raise AnalysisInputError("live service requires a LedgerSession")
        if not isinstance(delivery, TelegramDeliveryIntegration):
            raise AnalysisInputError("live service requires a TelegramDeliveryIntegration")
        primary = list(primary_window)
        if not primary:
            raise AnalysisInputError("live service requires a nonempty primary candle window")
        if set(higher_window) != set(runtime.higher_candles):
            raise AnalysisInputError(
                "higher window must cover exactly the runtime's higher timeframes"
            )
        self._feed = feed
        self._runtime = runtime
        self._session = session
        self._delivery = delivery
        self._window_store = window_store
        self._window_key = window_key
        self._destination_id = destination_id
        self._primary_window = primary
        self._higher_window: dict[str, list[OHLCV]] = {
            timeframe: list(candles) for timeframe, candles in higher_window.items()
        }
        self._last_primary: datetime | None = primary[-1].timestamp
        self._last_higher: dict[str, datetime | None] = {
            timeframe: (candles[-1].timestamp if candles else None)
            for timeframe, candles in self._higher_window.items()
        }
        self._cycles = 0

    # -- wiring exposed for operators and tests ---------------------------------

    @property
    def feed(self) -> LiveMarketFeed:
        return self._feed

    @property
    def runtime(self) -> LiveRuntime:
        return self._runtime

    @property
    def session(self) -> LedgerSession:
        """The live Phase 26G ledger session; the single authoritative ledger."""

        return self._session

    @property
    def delivery(self) -> TelegramDeliveryIntegration:
        return self._delivery

    @property
    def destination_id(self) -> str:
        return self._destination_id

    @property
    def ledger_key(self) -> str:
        return self._session.key

    @property
    def window_key(self) -> str:
        return self._window_key

    @property
    def cycles(self) -> int:
        return self._cycles

    @property
    def primary_window(self) -> tuple[OHLCV, ...]:
        """The retained primary candle window (read-only copy)."""

        return tuple(self._primary_window)

    # -- the one cycle -------------------------------------------------------------

    def run_cycle(self) -> CycleReport:
        """Poll once; extend the chain with new closed candles; persist; deliver."""

        update = self._feed.poll()
        higher_applied = 0
        for timeframe in sorted(update.higher):
            for candle in update.higher[timeframe]:
                if self._accept_higher(timeframe, candle):
                    self._runtime.extend_higher(timeframe, candle)
                    higher_applied += 1
        frames: list[SignalSnapshot] = []
        for candle in update.primary:
            if self._accept_primary(candle):
                frames.append(self._runtime.update(candle))
        ledger_persisted = False
        if frames:
            for frame in frames:
                self._session.update(frame)
            self._session.persist()
            self._persist_window()
            ledger_persisted = True
        buy_signals = 0
        states: list[DeliveryState] = []
        for frame in frames:
            if frame.status is SignalStatus.BUY_SIGNAL:
                buy_signals += 1
                result = self._delivery.deliver(frame, self._destination_id)
                states.append(result.state)
        self._cycles += 1
        return CycleReport(
            primary_candles=len(frames),
            higher_candles=higher_applied,
            frames=len(frames),
            buy_signals=buy_signals,
            delivery_states=tuple(states),
            ledger_persisted=ledger_persisted,
        )

    # -- window and cursor mechanics -------------------------------------------------

    def _accept_primary(self, candle: OHLCV) -> bool:
        if self._last_primary is not None and candle.timestamp <= self._last_primary:
            return False
        self._primary_window.append(candle)
        self._last_primary = candle.timestamp
        return True

    def _accept_higher(self, timeframe: str, candle: OHLCV) -> bool:
        if timeframe not in self._higher_window:
            raise AnalysisInputError(
                f"the feed reported an untracked higher timeframe: {timeframe}"
            )
        last = self._last_higher[timeframe]
        if last is not None and candle.timestamp <= last:
            return False
        self._higher_window[timeframe].append(candle)
        self._last_higher[timeframe] = candle.timestamp
        return True

    def _persist_window(self) -> None:
        dataset = ReplayDataset(
            symbol=self._runtime.series.symbol,
            timeframe=self._runtime.series.timeframe,
            candles=tuple(self._primary_window),
            higher_candles={
                timeframe: tuple(candles) for timeframe, candles in self._higher_window.items()
            },
            venue=self._runtime.series.venue,
            provider=self._runtime.series.provider,
            dataset_id=self._runtime.series.dataset_id,
        )
        self._window_store.save(self._window_key, dataset)


def build_telegram_delivery(
    config: LiveServiceConfig,
    *,
    transport: HttpTransport | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> TelegramDeliveryIntegration:
    """Assemble the existing Phase 24 delivery stack for the live service.

    Enabled outside dry-run: the real ``TelegramSink`` over the existing
    stdlib transport, with the token injected from the environment path and
    the chat id resolved through the frozen destination map. Dry-run or
    disabled: the existing ``OfflinePayloadSink`` — no Telegram client is
    constructed, so no configuration mistake can reach the network. The
    orchestration master switch mirrors ``enabled``; dedup and the attempt
    budget keep their frozen defaults.
    """

    real = config.enabled and not config.dry_run
    resolved_sleep = sleep_fn if sleep_fn is not None else _default_sleep
    resolved_now = now_fn if now_fn is not None else time.monotonic
    sink: TelegramSink | OfflinePayloadSink
    if real:
        if config.token is None or config.chat_id is None:
            raise LiveConfigurationError(
                "real Telegram delivery requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )
        sink = TelegramSink(
            config.token,
            config=TelegramConfig(enabled=True),
            destinations={DESTINATION_ID: config.chat_id},
            http_transport=transport,
            sleep_fn=resolved_sleep,
            now_fn=resolved_now,
        )
    else:
        sink = OfflinePayloadSink()
    orchestration = OrchestrationConfig(enabled=config.enabled)
    coordinator = DeliveryCoordinator(None, config=orchestration)
    # The frozen integration consumes any duck-typed PayloadSink (its own
    # constructor documents the injection contract); the cast records that
    # sanctioned structural boundary without weakening it.
    return TelegramDeliveryIntegration(
        cast(PayloadSink, sink),
        coordinator=coordinator,
        max_attempts=orchestration.max_attempts,
    )


def start_live_service(
    config: LiveServiceConfig,
    *,
    store: LedgerStore,
    window_store: DatasetStore,
    configuration: BacktestConfiguration | None = None,
    feed: LiveMarketFeed | None = None,
    delivery: TelegramDeliveryIntegration | None = None,
    market_transport: JsonTransport | None = None,
    transport: HttpTransport | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> LiveService:
    """Assemble the live service: warm historical state, then continue live.

    Startup order: restore the persisted candle window when present
    (deterministic restart) or take one initial feed poll as the warm-up
    window; rebuild the frozen analysis state over that window through
    ``LiveRuntime.warm_up``; open the Phase 26G ledger session over the
    regenerated warm-up frames (fresh bootstrap or verified recovery, exactly
    as frozen); save the window; return the service ready for ``run_cycle``.
    """

    try:
        market = config.market_data_config()
    except ValueError as exc:
        raise LiveConfigurationError(str(exc)) from exc
    pipeline = configuration if configuration is not None else BacktestConfiguration()
    if not isinstance(pipeline, BacktestConfiguration):
        raise AnalysisInputError("live service requires a BacktestConfiguration")
    series = SeriesProvenance(
        config.symbol, config.timeframe, LIVE_VENUE, market.data_source, LIVE_DATASET_ID
    )
    window_key = f"window:live:{config.symbol}:{config.timeframe}"
    ledger_key = f"ledger:live:{config.symbol}:{config.timeframe}"
    resolved_feed = (
        feed
        if feed is not None
        else LiveMarketFeed(
            market,
            higher_timeframes=config.higher_timeframes,
            transport=market_transport,
            clock=clock,
            sleep_fn=sleep_fn,
        )
    )
    resolved_delivery = (
        delivery
        if delivery is not None
        else build_telegram_delivery(config, transport=transport, sleep_fn=sleep_fn, now_fn=now_fn)
    )

    window = window_store.load(window_key)
    if window is not None:
        if (
            window.symbol != config.symbol
            or window.timeframe != config.timeframe
            or window.venue != LIVE_VENUE
            or window.dataset_id != LIVE_DATASET_ID
        ):
            raise LiveConfigurationError(
                "the persisted live window belongs to a different declared series"
            )
        runtime = LiveRuntime(pipeline, series=series, higher_candles=window.higher_candles)
        frames = runtime.warm_up(window.candles)
        primary_window: tuple[OHLCV, ...] = window.candles
        higher_window: dict[str, tuple[OHLCV, ...]] = dict(window.higher_candles)
    else:
        initial = resolved_feed.poll()
        if not initial.primary:
            raise LiveConfigurationError(
                "the initial poll produced no closed candles; the live service cannot start"
            )
        runtime = LiveRuntime(pipeline, series=series, higher_candles=initial.higher)
        frames = runtime.warm_up(initial.primary)
        primary_window = initial.primary
        higher_window = dict(initial.higher)

    session = open_ledger_session(store, ledger_key, pipeline.outcome_tracking, frames)
    service = LiveService(
        feed=resolved_feed,
        runtime=runtime,
        session=session,
        delivery=resolved_delivery,
        window_store=window_store,
        window_key=window_key,
        primary_window=primary_window,
        higher_window=higher_window,
    )
    service._persist_window()
    return service
