"""Phase 33/35A public API: the live signal service boundary (delivery-only, additive).

The live package composes existing frozen seams into one polling service:

    LiveMarketFeed (existing public Binance provider)
        → LiveRuntime (existing Phase 3-17 analyzers, replay order)
            → SignalSnapshot
                ├→ LedgerSession (existing Phase 26B/26G/26D outcome path)
                └→ TelegramDeliveryIntegration (existing Phase 24 delivery)

Phase 35A adds the operator-grade driver above that service — nothing inside it:

    LivePollLoop: clock → candle-boundary scheduler → bounded jitter
        → LiveService.run_cycle()
            success     → next absolute candle boundary
            recoverable → capped exponential backoff
            fatal       → stop and surface
        stop request → finish the current synchronous operation → exit cleanly

It introduces no trading, order, position, sizing, broker, or execution
capability; it does not modify the offline research path (``HistoricalReplay``,
Phase 26H, Phase 24, or any analysis module); it never imports a private
offline helper. Importing this package performs no IO, network call, signal
generation, or delivery — construction and explicit ``run_cycle``/``run``
calls do.
"""

from smcsignal.live.config import (
    DEFAULT_HIGHER_TIMEFRAMES,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_PROVIDER,
    LiveConfigurationError,
    LiveServiceConfig,
    load_live_config,
)
from smcsignal.live.configuration_binding import (
    LIVE_CONFIGURATION_KEY_PREFIX,
    LIVE_WINDOW_KEY_PREFIX,
    LiveConfigurationBinding,
    bind_live_configuration,
    configuration_identity,
    live_configuration_key,
    live_window_key,
)
from smcsignal.live.gap import (
    GapInfo,
    LiveGapError,
    detect_continuity_gap,
    detect_internal_gap,
    expected_next_timestamp,
    validate_batch_contiguity,
)
from smcsignal.live.gap_aware_feed import GapAwareLiveMarketFeed
from smcsignal.live.gap_aware_service import GapAwareLiveService, start_gap_aware_live_service
from smcsignal.live.market_feed import (
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    LiveFeedError,
    LiveFeedUpdate,
    LiveMarketFeed,
    first_new,
    interval_of,
)
from smcsignal.live.poll_loop import (
    DEFAULT_POLL_BACKOFF_BASE_SECONDS,
    DEFAULT_POLL_BACKOFF_MAX_SECONDS,
    DEFAULT_POLL_HISTORY_LIMIT,
    DEFAULT_POLL_JITTER_MAX_SECONDS,
    DEFAULT_POLL_JITTER_MIN_SECONDS,
    DEFAULT_POLL_WAIT_CHUNK_SECONDS,
    RECOVERABLE_ERRORS,
    CycleRunner,
    LivePollLoop,
    LivePollLoopConfig,
    PollCycleResult,
    PollOutcome,
    classify_failure,
    install_stop_signal_handlers,
    load_poll_loop_config,
    next_poll_time,
)
from smcsignal.live.retry_after import (
    MAX_RETRY_AFTER_SECONDS,
    RateLimitedFeedError,
    parse_retry_after,
)
from smcsignal.live.runtime import LiveRuntime
from smcsignal.live.service import (
    DESTINATION_ID,
    LIVE_DATASET_ID,
    LIVE_VENUE,
    CycleReport,
    LiveService,
    build_telegram_delivery,
    start_live_service,
)

__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "DEFAULT_HIGHER_TIMEFRAMES",
    "DEFAULT_POLL_BACKOFF_BASE_SECONDS",
    "DEFAULT_POLL_BACKOFF_MAX_SECONDS",
    "DEFAULT_POLL_HISTORY_LIMIT",
    "DEFAULT_POLL_JITTER_MAX_SECONDS",
    "DEFAULT_POLL_JITTER_MIN_SECONDS",
    "DEFAULT_POLL_WAIT_CHUNK_SECONDS",
    "DEFAULT_PROVIDER",
    "DEFAULT_RETRY_ATTEMPTS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "DESTINATION_ID",
    "GapInfo",
    "GapAwareLiveMarketFeed",
    "GapAwareLiveService",
    "LIVE_CONFIGURATION_KEY_PREFIX",
    "LIVE_DATASET_ID",
    "LIVE_VENUE",
    "LIVE_WINDOW_KEY_PREFIX",
    "LiveConfigurationBinding",
    "LiveConfigurationError",
    "LiveFeedError",
    "LiveFeedUpdate",
    "LiveGapError",
    "LiveMarketFeed",
    "LivePollLoop",
    "LivePollLoopConfig",
    "LiveRuntime",
    "LiveService",
    "LiveServiceConfig",
    "MAX_RETRY_AFTER_SECONDS",
    "PollCycleResult",
    "PollOutcome",
    "RateLimitedFeedError",
    "RECOVERABLE_ERRORS",
    "CycleReport",
    "CycleRunner",
    "bind_live_configuration",
    "build_telegram_delivery",
    "classify_failure",
    "configuration_identity",
    "detect_continuity_gap",
    "detect_internal_gap",
    "expected_next_timestamp",
    "first_new",
    "install_stop_signal_handlers",
    "interval_of",
    "live_configuration_key",
    "live_window_key",
    "load_live_config",
    "load_poll_loop_config",
    "next_poll_time",
    "parse_retry_after",
    "start_gap_aware_live_service",
    "start_live_service",
    "validate_batch_contiguity",
]
