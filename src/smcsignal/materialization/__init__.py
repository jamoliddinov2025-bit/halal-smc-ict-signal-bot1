"""Phase 30 materialization: verified declared-run input materialization.

This package resolves one Phase 29 ``DeclaredRunBinding`` into the actual
frozen inputs it declares: the Phase 27 ``ReplayDataset`` and the Phase 28
``BacktestConfiguration`` are loaded through their existing stores by the
recorded keys, each restored value's canonical identity is derived through the
existing Phase 27/28 public canon, and the result is returned as one frozen
``VerifiedRunInputs`` only after both identities match the ``dataset_digest``
and ``configuration_digest`` the binding pinned at save time.

It exists because restoring a binding and re-loading the stores it points at
was caller convention: nothing checked that the value found under a key was
still the value the binding declared. Materialization makes that check one
verified, read-only, deterministic step. It ends there — it never runs a
pipeline, creates a run, session, ledger, or report, calls the Phase 26H seam,
writes a document, or touches analytics, persistence, series, sessions, runs,
delivery, monitoring, data providers, or Telegram. The caller composes the
verified inputs into the frozen Phase 26H API itself.
"""

from .declared_inputs import VerifiedRunInputs, materialize_declared_inputs

__all__ = [
    "VerifiedRunInputs",
    "materialize_declared_inputs",
]
