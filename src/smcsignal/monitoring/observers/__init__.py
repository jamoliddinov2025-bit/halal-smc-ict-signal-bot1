"""Read-only observers: projections of records other layers already published.

An observer reads a published, immutable outcome and returns monitoring values of
its own. It never repairs, reorders, synthesizes, replaces, trims, or mutates what
it reads, and it holds no handle that could change a producer: the value types it
accepts are frozen records, and the values it returns are monitoring events and
metrics.

Phase 25B-4 ships the market-data observer only. The signal and delivery
observers are deliberately absent, so this package cannot observe a decision or
an outbound message yet.
"""

from smcsignal.monitoring.observers.data import (
    MARKET_DATA_METRICS,
    DataObservation,
    MarketDataObserver,
    series_subject,
)

__all__ = [
    "DataObservation",
    "MARKET_DATA_METRICS",
    "MarketDataObserver",
    "series_subject",
]
