"""Phase 26A public API: downstream analytics over the real signal pipeline.

The analytics layer is a strict leaf: it consumes immutable records the Phase
17 signal engine already published (``SignalSnapshot``) and finalized Phase 18
outcome versions produced by the existing market outcome evaluator. It never
imports ``smcsignal.delivery`` or ``smcsignal.monitoring``, never reads a
clock, file, or network, and nothing upstream may import it.

``DeliveryState`` is a transport fact and is never interpreted as a trade
outcome: a Telegram delivery success (``DELIVERED``) can never become a WIN,
LOSS, or BREAKEVEN. Only the market outcome evaluator can finalize an outcome;
until then observed outcomes stay OPEN.
"""

from smcsignal.analytics.models import (
    BREAKEVEN,
    MonthlyReport,
    MonthlySummary,
    SignalObservation,
    StrategyStats,
    strategy_stats,
)
from smcsignal.analytics.observer import AnalyticsObserver, ObservedSignalEngine

__all__ = [
    "BREAKEVEN",
    "AnalyticsObserver",
    "MonthlyReport",
    "MonthlySummary",
    "ObservedSignalEngine",
    "SignalObservation",
    "StrategyStats",
    "strategy_stats",
]
