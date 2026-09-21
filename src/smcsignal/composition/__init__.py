"""Phase 31 composition: the verified-run composition boundary.

One explicit adapter, ``execute_verified_run``, accepts an already-verified
Phase 30 ``VerifiedRunInputs`` together with a caller-supplied existing Phase
26D ``LedgerStore`` and hands the verified dataset, the verified
configuration, and the declared ledger key to the frozen Phase 26H
``run_declared_history`` seam exactly once, returning its ``LedgerSession``
unchanged.

It exists because the step between Phase 30's verified value and Phase 26H's
call was caller convention. The adapter re-verifies nothing (Phase 30 stays
authoritative for identity), reloads nothing (the Phase 27/28/29 stores are
never touched), duplicates nothing (frame regeneration, session opening,
recovery, and persistence stay the frozen 26F/26G machinery's, reached only
through 26H), and adds no persistence, format, digest, runtime, scheduler,
delivery, monitoring, CLI, exchange, or trading capability. Dependency
direction is one-way: materialization → composition → runs.
"""

from .verified_run import execute_verified_run

__all__ = [
    "execute_verified_run",
]
