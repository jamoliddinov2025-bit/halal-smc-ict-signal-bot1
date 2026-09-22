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

Restart support (Phase 35E)
---------------------------

Two durable inputs, one deterministic restart:

1. **Checkpoint** (preferred, Phase 35E) — the explicit versioned
   ``LiveCheckpoint`` holds configuration identity, series identity, cursors,
   the full primary/HTF recovery histories, and the duplicate-fencing set.
   Startup validates schema, identity, and integrity, replays the checkpoint
   primary candles through ``LiveRuntime.warm_up`` (the frozen Phase 26F/26G
   regeneration path — nothing private is injected), re-derives fencing, and
   fails closed if the regenerated fencing or latest signal id does not match
   the checkpoint. The Phase 26G ledger open then verifies against that same
   regenerated history exactly as before.

2. **Window** (legacy fallback) — the Phase 27 ``ReplayDataset`` under the
   live key, unchanged. When no checkpoint is configured, the window keeps
   its full history (legacy behavior); when a checkpoint store is configured,
   the window is bounded to the configuration-derived local context and the
   checkpoint is the recovery source.

Lost checkpoint with a bounded window (audit C4 — documented, not redesigned)
----------------------------------------------------------------------------

Exact code path, in ``start_live_service``:

    checkpoint_store is not None
        → checkpoint = checkpoint_store.load(checkpoint_key)
        → returns None (absent, deleted, or never written)
        → checkpoint is None, so the identity block above is skipped entirely
        → falls through to the ``else:`` branch
        → window = window_store.load(window_key)
        → runtime = LiveRuntime(pipeline, series, higher_candles=window.higher_candles)
        → frames = runtime.warm_up(window.candles)   # ONLY the bounded suffix
        → session = open_ledger_session(store, ledger_key, outcome_tracking, frames)

Because ``_apply_retention`` trims the *persisted* window to
``RetentionPolicy.local_context_bound`` after every successful checkpoint
write — and because ``run_cycle`` persists the window *before* it applies
retention — the window that survives a checkpoint loss holds the previous
cycle's bounded suffix plus the newest cycle's candles. Warm-up therefore
replays a truncated history, and the regenerated analyzer state is not, in
general, the state an uninterrupted run would hold.

Fail-closed status under the existing invariants — measured, not assumed
(``tests/live/test_phase35e_audit.py``):

* **Fail closed when the persisted ledger is non-empty** (the normal case —
  any published BUY leaves an observation). ``open_ledger_session`` is the
  Phase 26G *verified* open: it replays the supplied history and authorizes
  only on complete ``LedgerSnapshot`` equality. A truncated warm-up cannot
  reproduce the stored observations, so the open raises
  ``AnalysisInputError`` before any session exists, ``store.save`` is never
  reached, and the stored ledger bytes stay untouched. The service does not
  start.
* **Not detectable when the persisted ledger is still empty** (no BUY has
  ever been published). The verified open compares an empty fresh lifecycle
  against the empty stored snapshot and is satisfied before replaying a
  single frame, so a truncated warm-up is accepted and the runtime silently
  resumes from the persisted bounded window instead of the full history
  (measured in the fixture: 7 of 17 candles, and a different ``signal_id``
  for the same candle). This is a real residual gap; it is a property of the
  frozen Phase 26G verification seam, which authorizes on ledger content and
  therefore has nothing to compare when there is none. Closing it would
  require either an unbounded window or a checkpoint-presence invariant —
  both are C4 redesigns and are deliberately out of scope for this phase.

Cycle ordering (unchanged safety relationship, Phase 35E insertion):

    analysis → ledger persistence → window/checkpoint persistence → delivery

The Phase 35D outbox remains the only Telegram durability path. Checkpoint
persistence never touches the outbox, never enqueues a message, and never
runs before ledger persistence has succeeded.

Retention
---------

With a checkpoint store configured, service windows (``_primary_window`` /
``_higher_window``) are bounded after each successful checkpoint write to
``RetentionPolicy.local_context_bound`` — the derived
``max(fractal_length, atr_period + 1, sweep_lookback_bars,
max_candidate_lookback, 3)``. Duplicate fencing, MTF/OTE state, analyzer
state, and recovery histories stay in the runtime and the checkpoint; they
are never evicted from the window-bounding step.

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
from smcsignal.live.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointStore,
    LiveCheckpoint,
    LiveCheckpointError,
    live_checkpoint_key,
)
from smcsignal.live.config import LiveConfigurationError, LiveServiceConfig
from smcsignal.live.configuration_binding import configuration_identity
from smcsignal.live.market_feed import LiveMarketFeed
from smcsignal.live.retention import FVG_FRAME_WINDOW, RetentionPolicy
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
    checkpoint_persisted: bool = False


class LiveService:
    """Run one poll cycle: new closed candles in, ledger and delivery out.

    The service owns the live candle windows (bounded to the declared
    retention policy when a checkpoint store is configured) and the
    per-timeframe dedup cursors; the runtime owns analysis state; the ledger
    session and delivery integration stay the frozen downstream seams. The
    optional Phase 35E checkpoint store receives the explicit runtime export
    after ledger/window persistence and before any Telegram hand-off.
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
        checkpoint_store: CheckpointStore | None = None,
        checkpoint_key: str | None = None,
        retention: RetentionPolicy | None = None,
    ) -> None:
        if not isinstance(feed, LiveMarketFeed):
            raise AnalysisInputError("live service requires a LiveMarketFeed")
        if not isinstance(runtime, LiveRuntime):
            raise AnalysisInputError("live service requires a LiveRuntime")
        if not isinstance(session, LedgerSession):
            raise AnalysisInputError("live service requires a LedgerSession")
        if not isinstance(delivery, TelegramDeliveryIntegration):
            raise AnalysisInputError("live service requires a TelegramDeliveryIntegration")
        if checkpoint_store is not None and checkpoint_key is None:
            raise AnalysisInputError("checkpoint_key is required when checkpoint_store is set")
        if checkpoint_key is not None and checkpoint_store is None:
            raise AnalysisInputError("checkpoint_store is required when checkpoint_key is set")
        if retention is not None and not isinstance(retention, RetentionPolicy):
            raise AnalysisInputError("retention must be a RetentionPolicy or None")
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
        self._checkpoint_store = checkpoint_store
        self._checkpoint_key = checkpoint_key
        self._retention = retention
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
    def checkpoint_key(self) -> str | None:
        """The Phase 35E checkpoint key, when a checkpoint store is configured."""

        return self._checkpoint_key

    @property
    def checkpoint_store(self) -> CheckpointStore | None:
        return self._checkpoint_store

    @property
    def retention(self) -> RetentionPolicy | None:
        return self._retention

    @property
    def cycles(self) -> int:
        return self._cycles

    @property
    def primary_window(self) -> tuple[OHLCV, ...]:
        """The retained primary candle window (read-only copy)."""

        return tuple(self._primary_window)

    # -- the one cycle -------------------------------------------------------------

    def run_cycle(self) -> CycleReport:
        """Poll once; extend the chain with new closed candles; persist; deliver.

        Ordering is fixed: analysis → ledger persist → window persist →
        checkpoint persist → Telegram delivery through the Phase 35D outbox
        path. Delivery never runs before durable ledger state, and a
        checkpoint failure fails the cycle closed before any send.
        """

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
        checkpoint_persisted = False
        if frames:
            for frame in frames:
                self._session.update(frame)
            self._session.persist()
            self._persist_window()
            ledger_persisted = True
            checkpoint_persisted = self._persist_checkpoint()
            self._apply_retention()
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
            checkpoint_persisted=checkpoint_persisted,
        )

    # -- window, checkpoint, and cursor mechanics -------------------------------------

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

    def _persist_checkpoint(self) -> bool:
        """Export and durably persist the Phase 35E checkpoint; False if unconfigured."""

        if self._checkpoint_store is None or self._checkpoint_key is None:
            return False
        state = self._runtime.export_checkpoint_state()
        retention_bound = (
            self._retention.local_context_bound if self._retention is not None else FVG_FRAME_WINDOW
        )
        checkpoint = LiveCheckpoint(
            configuration_identity=configuration_identity(self._runtime.configuration),
            symbol=self._runtime.series.symbol,
            primary_timeframe=self._runtime.series.timeframe,
            higher_timeframes=tuple(self._runtime.mtf_config.higher_timeframes),
            series=self._runtime.series,
            last_primary=state.last_primary,
            last_higher=dict(state.last_higher),
            frame_count=state.frame_count,
            retention_local_bound=retention_bound,
            primary_candles=state.primary_candles,
            higher_candles=dict(state.higher_candles),
            published_setups=state.published_setups,
            latest_signal_id=state.latest_signal_id,
        )
        self._checkpoint_store.save(self._checkpoint_key, checkpoint)
        return True

    def _apply_retention(self) -> None:
        """Bound raw service windows after the recovery checkpoint is durable.

        Only the service-owned raw windows are trimmed. Runtime OTE/HTF
        context, analyzer state, fencing, and the persisted checkpoint are
        untouched — they are the retained recovery/analysis state.
        """

        if self._retention is None or self._checkpoint_store is None:
            return
        bound = self._retention.local_context_bound
        if len(self._primary_window) > bound:
            del self._primary_window[:-bound]
        for candles in self._higher_window.values():
            if len(candles) > bound:
                del candles[:-bound]


def build_telegram_delivery(
    config: LiveServiceConfig,
    *,
    transport: HttpTransport | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> TelegramDeliveryIntegration:
    """Assemble the existing Phase 24 delivery stack for the live service.

    Enabled outside dry-run: the real ``TelegramSink`` over the existing
    stdlib transport, with the token injected from the environment path and the
    chat id resolved through the frozen destination map. Dry-run or
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
    checkpoint_store: CheckpointStore | None = None,
    retention: RetentionPolicy | None = None,
) -> LiveService:
    """Assemble the live service: warm historical state, then continue live.

    Startup order (Phase 35E):

    1. Validate the declared configuration identity against a present
       checkpoint (schema, identity, integrity — fail closed on mismatch).
    2. If a valid checkpoint exists: rebuild the frozen runtime by warm-up
       replaying the checkpoint's primary candles, regenerate fencing, and
       fail closed when the regenerated fencing/latest id differs from the
       checkpoint (a restart can never silently re-publish).
    3. Else restore the Phase 27 window (legacy) or take one initial feed
       poll as the warm-up window — unchanged Phase 33 behavior. Note that a
       *configured* checkpoint store whose load returned ``None`` also lands
       here; with the window bounded that is the audit C4 path documented in
       the module docstring.
    4. Open the Phase 26G ledger session over the regenerated warm-up frames
       (fresh bootstrap or verified recovery, exactly as frozen).
    5. Persist the window, persist the checkpoint when configured, and return
       the service ready for ``run_cycle``.
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
    checkpoint_key = live_checkpoint_key(config.symbol, config.timeframe)
    resolved_retention = (
        retention if retention is not None else RetentionPolicy.from_configuration(pipeline)
    )
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

    declared_identity = configuration_identity(pipeline)
    checkpoint: LiveCheckpoint | None = None
    if checkpoint_store is not None:
        checkpoint = checkpoint_store.load(checkpoint_key)
        if checkpoint is not None:
            # Fail closed on any identity mismatch before touching the runtime.
            mismatches: list[str] = []
            # Schema gate first: a checkpoint written under a schema version
            # this build does not implement has no comparable field semantics,
            # so its version is checked against the constant this build
            # understands rather than against itself.
            if checkpoint.schema_version != CHECKPOINT_SCHEMA_VERSION:
                mismatches.append("schema_version")
            if checkpoint.configuration_identity != declared_identity:
                mismatches.append("configuration_identity")
            if checkpoint.symbol != config.symbol:
                mismatches.append("symbol")
            if checkpoint.primary_timeframe != config.timeframe:
                mismatches.append("primary_timeframe")
            if checkpoint.higher_timeframes != tuple(pipeline.mtf.higher_timeframes):
                mismatches.append("higher_timeframes")
            if checkpoint.series != series:
                mismatches.append("series")
            if mismatches:
                raise LiveCheckpointError(
                    "checkpoint does not match the current runtime declaration (schema "
                    f"{CHECKPOINT_SCHEMA_VERSION} expected; mismatched: "
                    + ", ".join(mismatches)
                    + "); refusing to restore a checkpoint from another configuration"
                )

    if checkpoint is not None:
        runtime = LiveRuntime(pipeline, series=series, higher_candles=checkpoint.higher_candles)
        frames = runtime.warm_up(checkpoint.primary_candles)
        state = runtime.export_checkpoint_state()
        if state.published_setups != checkpoint.published_setups:
            raise LiveCheckpointError(
                "regenerated duplicate-fencing set does not match the checkpoint; "
                "refusing to resume — the checkpoint and recovery history disagree"
            )
        if state.latest_signal_id != checkpoint.latest_signal_id:
            raise LiveCheckpointError(
                "regenerated latest signal id does not match the checkpoint; "
                "refusing to resume — the checkpoint and recovery history disagree"
            )
        if runtime.processed_count != checkpoint.frame_count:
            raise LiveCheckpointError(
                "regenerated frame count does not match the checkpoint; refusing to resume"
            )
        primary_window: tuple[OHLCV, ...] = tuple(checkpoint.primary_candles)
        higher_window: dict[str, tuple[OHLCV, ...]] = {
            tf: tuple(candles) for tf, candles in checkpoint.higher_candles.items()
        }
    else:
        # Audit C4 path: a configured checkpoint store whose load returned
        # None (checkpoint absent/deleted) lands here, so the recovery source
        # is the Phase 27 window — which, with a checkpoint store configured,
        # holds only the bounded suffix. See the module docstring's "Lost
        # checkpoint with a bounded window" section for the exact fail-closed
        # analysis: the Phase 26G verified ledger open below refuses whenever
        # the stored ledger has content it cannot reproduce.
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
            primary_window = window.candles
            higher_window = dict(window.higher_candles)
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
        checkpoint_store=checkpoint_store,
        checkpoint_key=checkpoint_key if checkpoint_store is not None else None,
        retention=resolved_retention,
    )
    service._persist_window()
    service._persist_checkpoint()
    service._apply_retention()
    return service
