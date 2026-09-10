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

Phase 26B adds ``SignalOutcomeLifecycle``: the deterministic composition that
observes each published frame, evaluates open outcomes through the unchanged
Phase 18 analyzer, and records the evaluator's finals into the observer's
ledger — closing the publication-to-statistics loop without any new arithmetic.

Phase 26C adds the deterministic ledger snapshot and restore layer
(``LedgerSnapshot``, ``snapshot_ledger``, ``ledger_bytes``,
``load_ledger_bytes``, ``RestoredAnalyticsLedger``): canonical,
content-addressed bytes through the existing evidence canon, restored exactly
through the frozen model constructors. Bytes only — no IO; restored ledgers
are read-only historical facts, and evaluator continuation is a future phase.
"""

from smcsignal.analytics.ledger import (
    METHODOLOGY_VERSION as LEDGER_METHODOLOGY_VERSION,
)
from smcsignal.analytics.ledger import (
    LedgerSnapshot,
    RestoredAnalyticsLedger,
    ledger_bytes,
    load_ledger_bytes,
    snapshot_ledger,
)
from smcsignal.analytics.lifecycle import (
    LifecycleStep,
    SignalOutcomeLifecycle,
    run_lifecycle,
)
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
    "LEDGER_METHODOLOGY_VERSION",
    "AnalyticsObserver",
    "LedgerSnapshot",
    "LifecycleStep",
    "MonthlyReport",
    "MonthlySummary",
    "ObservedSignalEngine",
    "RestoredAnalyticsLedger",
    "SignalObservation",
    "SignalOutcomeLifecycle",
    "StrategyStats",
    "ledger_bytes",
    "load_ledger_bytes",
    "run_lifecycle",
    "snapshot_ledger",
    "strategy_stats",
]
