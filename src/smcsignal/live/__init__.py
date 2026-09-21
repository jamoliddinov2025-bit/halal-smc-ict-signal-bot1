"""Phase 33 public API: the live signal service boundary (delivery-only, additive).

The live package composes existing frozen seams into one polling service:

    LiveMarketFeed (existing public Binance provider)
        → LiveRuntime (existing Phase 3-17 analyzers, replay order)
            → SignalSnapshot
                ├→ LedgerSession (existing Phase 26B/26G/26D outcome path)
                └→ TelegramDeliveryIntegration (existing Phase 24 delivery)

It introduces no trading, order, position, sizing, broker, or execution
capability; it does not modify the offline research path (``HistoricalReplay``,
Phase 26H, Phase 24, or any analysis module); it never imports a private
offline helper. Importing this package performs no IO, network call, signal
generation, or delivery — construction and explicit ``run_cycle`` calls do.
"""

from smcsignal.live.config import (
    DEFAULT_HIGHER_TIMEFRAMES,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_PROVIDER,
    LiveConfigurationError,
    LiveServiceConfig,
    load_live_config,
)
from smcsignal.live.market_feed import (
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    LiveFeedError,
    LiveFeedUpdate,
    LiveMarketFeed,
    first_new,
    interval_of,
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
    "DEFAULT_PROVIDER",
    "DEFAULT_RETRY_ATTEMPTS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "DESTINATION_ID",
    "LIVE_DATASET_ID",
    "LIVE_VENUE",
    "CycleReport",
    "LiveConfigurationError",
    "LiveFeedError",
    "LiveFeedUpdate",
    "LiveMarketFeed",
    "LiveRuntime",
    "LiveService",
    "LiveServiceConfig",
    "build_telegram_delivery",
    "first_new",
    "interval_of",
    "load_live_config",
    "start_live_service",
]
