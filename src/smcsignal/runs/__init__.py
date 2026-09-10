"""Phase 26H public API: the deterministic offline composition seam.

One sanctioned composition edge: ``run_declared_history`` runs a declared
series history through the frozen Phase 26F frame-regeneration seam into a
frozen Phase 26G ledger session, so frame generation and outcome evaluation
are permanently bound to the same declared configuration.

This package is deterministic offline composition only — it is not a live
runtime, owns no frame source, and adds no coordinator, state machine, or
result model. Nothing upstream imports it; ``smcsignal.series`` and
``smcsignal.sessions`` remain unaware of it.
"""

from smcsignal.runs.series_run import run_declared_history

__all__ = [
    "run_declared_history",
]
