"""Phase 35C: gap-aware live service — additive wrapper over frozen service.

Ensures no incomplete/gapped primary window reaches frozen runtime.

- Subclasses LiveService so isinstance checks still pass where needed? LiveService
  does not check isinstance of self, only of its dependencies. Subclassing is safe.
- Overrides run_cycle() to validate continuity between persisted window's last
  primary timestamp and new primary candles before any runtime/ledger/window/
  delivery mutation.
- Uses gap.py primitives (expected_next_timestamp, detect_internal_gap,
  detect_continuity_gap).
- Raises LiveGapError on gap — recoverable via LiveFeedError subclass, so
  poll loop treats as recoverable failure without modifying frozen poll_loop.
- No ledger/window/delivery mutation on gap — raises before any mutation.

This seam does not modify frozen service.py, runtime.py, market_feed.py,
config.py, poll_loop.py, configuration_binding.py.

It is intended to be used via composition: start_gap_aware_live_service()
mirrors start_live_service() but returns GapAwareLiveService.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from smcsignal.analysis.backtest import BacktestConfiguration, ReplayDataset
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.mtf.timeframes import timeframe_seconds
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus
from smcsignal.data.binance import JsonTransport
from smcsignal.data.models import OHLCV
from smcsignal.datasets import DatasetStore
from smcsignal.delivery.models import DeliveryState
from smcsignal.delivery.telegram.http import HttpTransport
from smcsignal.delivery.telegram.integration import TelegramDeliveryIntegration
from smcsignal.live.config import LiveConfigurationError, LiveServiceConfig
from smcsignal.live.gap import (
    LiveGapError,
    detect_continuity_gap,
    detect_internal_gap,
)
from smcsignal.live.gap_aware_feed import GapAwareLiveMarketFeed
from smcsignal.live.market_feed import LiveMarketFeed
from smcsignal.live.runtime import LiveRuntime
from smcsignal.persistence import LedgerStore
from smcsignal.sessions import LedgerSession, open_ledger_session

DESTINATION_ID = "primary"
LIVE_VENUE = "binance_spot"
LIVE_DATASET_ID = "live-feed:v1"


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


class GapAwareLiveService:
    """Gap-aware variant of LiveService — same public surface, plus gap check.

    Owns same state as LiveService (feed, runtime, session, delivery, window
    store, window key, primary/higher windows, cursors). Gap validation happens
    at start of run_cycle() before any mutation.

    This class intentionally does NOT inherit from LiveService to avoid
    inheriting frozen __init__ validation that requires LiveMarketFeed isinstance
    (GapAwareLiveMarketFeed is subclass, so isinstance passes, but we still
    re-implement to keep explicit). However we provide same properties as
    LiveService for compatibility with tests and poll loop (CycleRunner protocol).
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
        # Interval for primary gap detection
        self._primary_interval = timeframe_seconds(runtime.series.timeframe)

    @property
    def feed(self) -> LiveMarketFeed:
        return self._feed

    @property
    def runtime(self) -> LiveRuntime:
        return self._runtime

    @property
    def session(self) -> LedgerSession:
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
        return tuple(self._primary_window)

    def run_cycle(self):
        """Poll, validate gaps, then extend chain — fail closed on gap."""

        # Poll — feed itself may raise LiveGapError or RateLimitedFeedError
        update = self._feed.poll()

        # --- GAP VALIDATION BEFORE ANY MUTATION ---
        # Primary continuity vs last_primary
        if self._last_primary is not None and update.primary:
            gap = detect_continuity_gap(
                self._last_primary,
                update.primary,
                self._runtime.series.timeframe,
                history_limit=self._feed.config.history_limit,
            )
            if gap is not None:
                raise LiveGapError(gap)

        # Internal gaps within primary new batch
        if update.primary:
            internal = detect_internal_gap(
                update.primary,
                self._runtime.series.timeframe,
                history_limit=self._feed.config.history_limit,
            )
            if internal is not None:
                raise LiveGapError(internal)

        # Internal gaps within higher batches (optional, but safe)
        for tf, candles in update.higher.items():
            if candles:
                hg = detect_internal_gap(candles, tf, history_limit=self._feed.config.history_limit)
                if hg is not None:
                    raise LiveGapError(hg)

        # --- No gap → proceed with frozen logic (copied from LiveService) ---
        higher_applied = 0
        for timeframe in sorted(update.higher):
            for candle in update.higher[timeframe]:
                if self._accept_higher(timeframe, candle):
                    self._runtime.extend_higher(timeframe, candle)
                    higher_applied += 1

        from smcsignal.live.service import CycleReport

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
):
    """Reuse frozen build_telegram_delivery from service.py to avoid duplication."""

    from smcsignal.live.service import build_telegram_delivery as frozen_build

    return frozen_build(config, transport=transport, sleep_fn=sleep_fn, now_fn=now_fn)


def start_gap_aware_live_service(
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
) -> GapAwareLiveService:
    """Assemble gap-aware live service — same startup order as frozen service.

    Validates internal contiguity of initial batch and ensures window
    persistence only after successful validation.
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
        else GapAwareLiveMarketFeed(
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
        # Validate internal contiguity of restored window itself
        from smcsignal.live.gap import validate_batch_contiguity

        gap = validate_batch_contiguity(
            window.candles, config.timeframe, history_limit=market.history_limit
        )
        if gap is not None:
            raise LiveGapError(gap, message="persisted window itself has internal gap")

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
        # Internal gap check already done in feed, but double-check for startup
        from smcsignal.live.gap import validate_batch_contiguity

        gap = validate_batch_contiguity(
            initial.primary, config.timeframe, history_limit=market.history_limit
        )
        if gap is not None:
            raise LiveGapError(gap, message="initial poll batch has internal gap")

        runtime = LiveRuntime(pipeline, series=series, higher_candles=initial.higher)
        frames = runtime.warm_up(initial.primary)
        primary_window = initial.primary
        higher_window = dict(initial.higher)

    session = open_ledger_session(store, ledger_key, pipeline.outcome_tracking, frames)
    service = GapAwareLiveService(
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
