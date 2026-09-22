"""Phase 35D live wiring: the durable delivery outbox over the frozen stack.

This module is the only approved composition point between the frozen live
service and the durable outbox core (``smcsignal.delivery.outbox``). It adds
no engine, no transport, no scheduler, and no thread of its own:

    build_outbox_telegram_delivery
        real mode  → frozen TelegramSink wrapped by OutboxPayloadSink
                     (durable intent before the call, durable receipt after)
        dry-run /  → the frozen builder verbatim; no outbox store is
        disabled     constructed and no durable file can ever appear

    start_outbox_gap_aware_live_service
        mirrors the frozen gap-aware startup, then runs deterministic
        reconciliation: warm-up frames of the durable window are compared
        against the durable outbox records and missing intents are recreated
        — bounded by the window, gated on the activation marker, never
        re-enqueueing a terminal record, and never re-broadcasting candles
        older than the activation moment.

    OutboxDrainingRunner
        a CycleRunner wrapper: it delegates ``run_cycle()`` unchanged and
        then performs one bounded drain pass, piggybacking the frozen candle
        scheduler. There is no second scheduler and no timer of any kind; the
        Phase 35C Retry-After-aware loop remains the scheduling authority.

Dry-run and disabled delivery can never create durable production delivery
state: the outbox is constructed if and only if the frozen real-mode decision
(``enabled and not dry_run``) holds.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.data.binance import JsonTransport
from smcsignal.data.models import OHLCV
from smcsignal.datasets import DatasetStore
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.outbox.models import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_PER_DRAIN,
    MAX_MAX_ATTEMPTS,
    MIN_MAX_ATTEMPTS,
    OutboxConfig,
)
from smcsignal.delivery.outbox.reconcile import reconcile_missing
from smcsignal.delivery.outbox.sink import OutboxPayloadSink
from smcsignal.delivery.outbox.store import FileOutboxStore, OutboxStoreError
from smcsignal.delivery.telegram.config import TelegramConfig
from smcsignal.delivery.telegram.http import HttpTransport
from smcsignal.delivery.telegram.integration import TelegramDeliveryIntegration
from smcsignal.delivery.telegram.sink import TelegramSink
from smcsignal.delivery.transport import PayloadSink
from smcsignal.live.config import LiveConfigurationError, LiveServiceConfig
from smcsignal.live.gap import LiveGapError, validate_batch_contiguity
from smcsignal.live.gap_aware_feed import GapAwareLiveMarketFeed
from smcsignal.live.gap_aware_service import GapAwareLiveService
from smcsignal.live.market_feed import LiveMarketFeed
from smcsignal.live.poll_loop import CycleRunner
from smcsignal.live.runtime import LiveRuntime
from smcsignal.live.service import (
    DESTINATION_ID,
    LIVE_DATASET_ID,
    LIVE_VENUE,
    CycleReport,
    build_telegram_delivery,
)
from smcsignal.persistence import LedgerStore
from smcsignal.sessions import open_ledger_session

_OUTBOX_DIR = "LIVE_OUTBOX_DIR"
_OUTBOX_MAX_ATTEMPTS = "LIVE_OUTBOX_MAX_ATTEMPTS"
_OUTBOX_COOLDOWN_SECONDS = "LIVE_OUTBOX_COOLDOWN_SECONDS"
_OUTBOX_MAX_PER_DRAIN = "LIVE_OUTBOX_MAX_PER_DRAIN"


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OutboxSettings:
    """Validated operator settings for the durable outbox (no secrets)."""

    root: str
    config: OutboxConfig

    def __post_init__(self) -> None:
        if not isinstance(self.root, str) or not self.root.strip():
            raise LiveConfigurationError("the outbox root must be a nonempty path")
        if not isinstance(self.config, OutboxConfig):
            raise LiveConfigurationError("the outbox settings require an OutboxConfig")


def load_outbox_settings(source: Mapping[str, str] | None = None) -> OutboxSettings:
    """Load strict outbox settings from the environment mapping.

    ``LIVE_OUTBOX_DIR`` is required; the numeric policy values fall back to
    the locked defaults and are validated exactly (attempts 1-10, cooldown a
    finite nonnegative number of seconds, drain cap a positive integer).
    Loading starts nothing and writes nothing.
    """
    env = os.environ if source is None else source
    root = env.get(_OUTBOX_DIR)
    if root is None or not root.strip():
        raise LiveConfigurationError(f"{_OUTBOX_DIR} is required for durable delivery")
    raw_attempts = env.get(_OUTBOX_MAX_ATTEMPTS)
    attempts = DEFAULT_MAX_ATTEMPTS
    if raw_attempts is not None and raw_attempts.strip():
        try:
            attempts = int(raw_attempts.strip())
        except ValueError as exc:
            raise LiveConfigurationError(
                f"{_OUTBOX_MAX_ATTEMPTS} must be an integer, got {raw_attempts!r}"
            ) from exc
        if not MIN_MAX_ATTEMPTS <= attempts <= MAX_MAX_ATTEMPTS:
            raise LiveConfigurationError(
                f"{_OUTBOX_MAX_ATTEMPTS} must be between {MIN_MAX_ATTEMPTS} and {MAX_MAX_ATTEMPTS}"
            )
    raw_cooldown = env.get(_OUTBOX_COOLDOWN_SECONDS)
    cooldown = DEFAULT_COOLDOWN_SECONDS
    if raw_cooldown is not None and raw_cooldown.strip():
        try:
            cooldown = float(raw_cooldown.strip())
        except ValueError as exc:
            raise LiveConfigurationError(
                f"{_OUTBOX_COOLDOWN_SECONDS} must be a number of seconds, got {raw_cooldown!r}"
            ) from exc
        if not math.isfinite(cooldown) or cooldown < 0:
            raise LiveConfigurationError(f"{_OUTBOX_COOLDOWN_SECONDS} must be finite and >= 0")
    raw_drain = env.get(_OUTBOX_MAX_PER_DRAIN)
    drain_cap = DEFAULT_MAX_PER_DRAIN
    if raw_drain is not None and raw_drain.strip():
        try:
            drain_cap = int(raw_drain.strip())
        except ValueError as exc:
            raise LiveConfigurationError(
                f"{_OUTBOX_MAX_PER_DRAIN} must be an integer, got {raw_drain!r}"
            ) from exc
        if drain_cap < 1:
            raise LiveConfigurationError(f"{_OUTBOX_MAX_PER_DRAIN} must be a positive integer")
    return OutboxSettings(
        root=root.strip(),
        config=OutboxConfig(
            max_attempts=attempts,
            cooldown_seconds=cooldown,
            max_per_drain=drain_cap,
        ),
    )


def build_outbox_telegram_delivery(
    config: LiveServiceConfig,
    settings: OutboxSettings | None = None,
    *,
    transport: HttpTransport | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[TelegramDeliveryIntegration, OutboxPayloadSink | None]:
    """Assemble delivery exactly like the frozen builder, plus the outbox.

    Real mode constructs the frozen transport sink and wraps it in the
    durable outbox sink; the returned integration injects that wrapper. Dry
    run or disabled delivery delegates verbatim to the frozen builder and
    returns ``None`` for the outbox sink — no store is constructed and no
    durable file can appear.
    """
    real = config.enabled and not config.dry_run
    if not real:
        return (
            build_telegram_delivery(config, transport=transport, sleep_fn=sleep_fn, now_fn=now_fn),
            None,
        )
    if settings is None:
        raise LiveConfigurationError(
            "real durable delivery requires outbox settings (LIVE_OUTBOX_DIR)"
        )
    if config.token is None or config.chat_id is None:
        raise LiveConfigurationError(
            "real Telegram delivery requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        )
    resolved_sleep = sleep_fn if sleep_fn is not None else _default_sleep
    resolved_now = now_fn if now_fn is not None else time.monotonic
    store = FileOutboxStore(settings.root)
    inner = TelegramSink(
        config.token,
        config=TelegramConfig(enabled=True),
        destinations={DESTINATION_ID: config.chat_id},
        http_transport=transport,
        sleep_fn=resolved_sleep,
        now_fn=resolved_now,
    )
    # The frozen integration consumes any duck-typed PayloadSink (the same
    # sanctioned structural boundary the frozen live service records).
    sink = OutboxPayloadSink(store, cast(PayloadSink, inner), config=settings.config, clock=clock)
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=True))
    integration = TelegramDeliveryIntegration(
        sink,
        coordinator=coordinator,
        max_attempts=1,
    )
    return integration, sink


class OutboxDrainingRunner:
    """CycleRunner wrapper performing one bounded drain pass per cycle.

    The wrapped runner's ``run_cycle()`` is delegated unchanged. After the
    cycle — whether it returned or raised — one bounded, cooldown-aware drain
    pass runs, because delivery health is independent of feed health. The
    cycle's own outcome always wins: a cycle exception is re-raised exactly
    as produced, and a drain failure can never mask it. When the cycle
    succeeded, a drain failure propagates (fail closed).
    """

    def __init__(
        self,
        runner: CycleRunner,
        sink: OutboxPayloadSink,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(runner, CycleRunner) or not callable(runner.run_cycle):
            raise LiveConfigurationError(
                "OutboxDrainingRunner requires a CycleRunner exposing run_cycle()"
            )
        if not isinstance(sink, OutboxPayloadSink):
            raise LiveConfigurationError("OutboxDrainingRunner requires an OutboxPayloadSink")
        self._runner = runner
        self._sink = sink
        self._clock = clock if clock is not None else _utc_now

    @property
    def runner(self) -> CycleRunner:
        return self._runner

    @property
    def sink(self) -> OutboxPayloadSink:
        return self._sink

    def run_cycle(self) -> CycleReport:
        cycle_failure: Exception | None = None
        report: CycleReport | None = None
        try:
            report = self._runner.run_cycle()
        except Exception as exc:
            cycle_failure = exc
        try:
            self._sink.drain(self._clock())
        except Exception:
            if cycle_failure is None:
                raise
            # The cycle exception takes precedence; the drain failure stays
            # attached as the raising context for operator inspection.
        if cycle_failure is not None:
            raise cycle_failure
        assert report is not None
        return report


@dataclass(frozen=True, slots=True)
class OutboxLiveDeployment:
    """The assembled durable-delivery live deployment.

    ``runner`` is the object to hand to the frozen poll loop: the draining
    wrapper when the outbox is active, or the bare service when delivery is
    dry-run/disabled (or a caller-injected delivery is used). Reconciliation
    facts are surfaced for operator visibility.
    """

    service: GapAwareLiveService
    runner: CycleRunner
    outbox_sink: OutboxPayloadSink | None
    reconciliation_created: tuple[str, ...]
    reconciliation_disabled_reason: str | None


def start_outbox_gap_aware_live_service(
    config: LiveServiceConfig,
    *,
    store: LedgerStore,
    window_store: DatasetStore,
    settings: OutboxSettings | None = None,
    configuration: BacktestConfiguration | None = None,
    feed: LiveMarketFeed | None = None,
    delivery: TelegramDeliveryIntegration | None = None,
    market_transport: JsonTransport | None = None,
    transport: HttpTransport | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> OutboxLiveDeployment:
    """Assemble the gap-aware live service with the durable outbox.

    Startup mirrors the frozen gap-aware builder exactly — window restore
    with the Phase 35B binding check and Phase 35C contiguity checks,
    deterministic warm-up, verified ledger open, window persistence — and
    then runs reconciliation over the same warm-up frames when the outbox is
    active. A caller-injected ``delivery`` is used verbatim and receives no
    outbox behavior (the injection parity of the frozen builder).
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

    outbox_sink: OutboxPayloadSink | None
    if delivery is not None:
        resolved_delivery = delivery
        outbox_sink = None
    else:
        resolved_delivery, outbox_sink = build_outbox_telegram_delivery(
            config,
            settings,
            transport=transport,
            sleep_fn=sleep_fn,
            now_fn=now_fn,
            clock=clock,
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

    created: tuple[str, ...] = ()
    disabled_reason: str | None = None
    if outbox_sink is not None:
        resolved_clock = clock if clock is not None else _utc_now
        outbox_store = outbox_sink.store
        try:
            activated_at = outbox_store.ensure_meta(resolved_clock())
        except OutboxStoreError:
            disabled_reason = outbox_store.meta_failure
        else:
            report = reconcile_missing(
                frames,
                DESTINATION_ID,
                activated_at=activated_at,
                store=outbox_store,
                config=outbox_sink.config,
                clock=resolved_clock,
            )
            created = report.created

    runner: CycleRunner = (
        OutboxDrainingRunner(service, outbox_sink, clock=clock)
        if outbox_sink is not None
        else service
    )
    return OutboxLiveDeployment(
        service=service,
        runner=runner,
        outbox_sink=outbox_sink,
        reconciliation_created=created,
        reconciliation_disabled_reason=disabled_reason,
    )


__all__ = [
    "OutboxDrainingRunner",
    "OutboxLiveDeployment",
    "OutboxSettings",
    "build_outbox_telegram_delivery",
    "load_outbox_settings",
    "start_outbox_gap_aware_live_service",
]
