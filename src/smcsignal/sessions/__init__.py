"""Phase 26G public API: the single-series ledger session.

One caller-driven composition over the frozen arc: ``open_ledger_session``
opens a series' analytics ledger against a Phase 26D store — fresh bootstrap
under the explicitly supplied outcome configuration when no entry exists, or
verified replay-based recovery when one does — and returns a
``LedgerSession`` whose ``update()`` continues through the real Phase 26B
lifecycle and whose ``persist()`` checkpoints through the store.

The package imports only ``smcsignal.analytics``, ``smcsignal.persistence``,
and frozen Phase 17/18 record types. It owns no frame source, no second
ledger model, no clock, no scheduler, no concurrency, and no fleet; history
frames are caller-supplied values. Nothing here is a run loop, an application
runtime, or a service framework.
"""

from smcsignal.sessions.ledger_session import LedgerSession, open_ledger_session

__all__ = [
    "LedgerSession",
    "open_ledger_session",
]
