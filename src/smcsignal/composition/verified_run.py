"""Phase 31: the verified-run composition boundary — one adapter, one delegated call.

Phase 30 ends at a value: ``VerifiedRunInputs`` holds the declared dataset,
the declared configuration, and the declared ledger key of one historical run
only after both restored inputs proved to be exactly what their Phase 29
binding pinned. Phase 26H begins at a call: ``run_declared_history`` runs one
dataset and one configuration into a durable, verified ``LedgerSession``
against a caller-owned Phase 26D store. What sat between them was caller
convention — unpack the verified value and pass its three parts to the seam
in the right order. Phase 31 makes that hand-off one explicit function.

What the adapter does
---------------------

``execute_verified_run`` accepts an already-verified ``VerifiedRunInputs`` and
a caller-supplied existing ``LedgerStore``, and calls the frozen Phase 26H
seam exactly once with ``inputs.dataset``, ``inputs.configuration``, the
caller's store, and ``inputs.ledger_key``. It returns the seam's
``LedgerSession`` unchanged — provenance, continuation, and checkpoint
machinery intact. The only decision it makes is to refuse anything that is
not a ``VerifiedRunInputs``; that keeps the boundary honest, because the
Phase 30 constructor *is* the identity verification, and a value of that
type cannot exist around inputs that differ from what its binding declares.

What this boundary is NOT
-------------------------

- No re-verification: Phase 30 remains the sole authority on input identity.
  The adapter derives no identity of its own and consults no canon.
- No reloading: it never touches the Phase 27, 28, or 29 stores, so nothing
  can be substituted between verification and execution.
- No second engine: frame regeneration, session opening, fresh bootstrap,
  verified recovery, atomicity, and the one approved open-time persistence
  write all remain the frozen Phase 26F/26G machinery's, reached only
  through Phase 26H. Nothing is duplicated, retried, defaulted, or wrapped;
  every exception the seam raises propagates unchanged.
- No persistence of its own: no document, format, key scheme, temporary
  file, cache, or store; the only IO in the whole call is the caller's
  Phase 26D store, exactly as Phase 26H already channels it.
- No runtime: no scheduler, clock, feed, network, exchange, trading, order,
  delivery, monitoring, or CLI. Deterministic by construction — identical
  inputs and store contents yield the identical session.

Dependency direction is one-way: materialization → this adapter → runs.
Nothing upstream imports it; Phases 26H and 27–30 remain unaware of it.
"""

from __future__ import annotations

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.materialization import VerifiedRunInputs
from smcsignal.persistence import LedgerStore
from smcsignal.runs import run_declared_history
from smcsignal.sessions import LedgerSession


def execute_verified_run(inputs: VerifiedRunInputs, store: LedgerStore) -> LedgerSession:
    """Hand one verified run's inputs to the frozen Phase 26H seam, once.

    ``inputs`` must be a Phase 30 ``VerifiedRunInputs`` — anything else is
    refused with ``AnalysisInputError`` before the seam is reached, because
    only that type carries the guarantee that its dataset and configuration
    are the ones its binding declared. ``store`` is the caller's existing
    Phase 26D ``LedgerStore``; it is passed through untouched.

    The call is ``run_declared_history(inputs.dataset, inputs.configuration,
    store, inputs.ledger_key)`` and nothing more: the returned
    ``LedgerSession`` is the seam's own result, returned as is, and every
    exception the seam raises propagates unchanged. Nothing is reloaded,
    re-verified, retried, cached, or written by this function itself.
    """

    if not isinstance(inputs, VerifiedRunInputs):
        raise AnalysisInputError("execute_verified_run requires a Phase 30 VerifiedRunInputs")
    return run_declared_history(inputs.dataset, inputs.configuration, store, inputs.ledger_key)
