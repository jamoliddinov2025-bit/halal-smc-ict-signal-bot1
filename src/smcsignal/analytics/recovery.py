"""Phase 26E: ledger recovery and continuation — verified replay-based restart.

The Phase 26A-26D arc can observe, evaluate, finalize, snapshot, and durably
store the publication ledger — but a restarted process could only *read* its
history (Phase 26C ``RestoredAnalyticsLedger``), never *continue* it. Phase 26E
closes that gap with exactly one capability: turning a Phase 26C snapshot plus
the series' regenerated frame history into a **verified live Phase 26B
lifecycle** that continues exactly as if no restart had occurred.

Replay, not checkpointing (LOCK 1)
----------------------------------

Recovery never touches Phase 18 evaluator internals: it checkpoints nothing,
serializes no evaluator state, and deserializes none. It builds a fresh Phase
26B lifecycle — fresh observer, fresh evaluator — under the configuration
carried by the expected Phase 26C snapshot, and replays the supplied
historical ``SignalSnapshot`` frames through the unchanged 26B machinery
(``update()``: evaluate, observe, forward finals). Because Phases 3-18 are
deterministic, replaying the identical frame history reproduces the identical
ledger; the stored snapshot is then the verification oracle, not the state
source.

Verification and atomic refusal (LOCK 2)
----------------------------------------

After the replay, the reconstructed lifecycle is projected through the Phase
26C ``snapshot_ledger()`` and must equal the expected snapshot exactly —
observations, OPEN and finalized outcomes, ordering, settings, configuration
hash, and the content-addressed ``snapshot_id``. Any difference raises
``AnalysisInputError`` before anything is returned: no partially recovered
lifecycle ever escapes, the supplied expected snapshot is never mutated (it is
frozen), and the failure is deterministic. Sequence violations (replayed,
out-of-order, or gapped frames) raise inside the frozen Phase 18 evaluator
before any ledger state changes — inherited 26B atomicity, re-implemented by
nothing here.

Continuation semantics
----------------------

On success, ``RecoveredLedger.lifecycle`` is an ordinary Phase 26B lifecycle:
feeding it further frames behaves exactly like uninterrupted operation — new
BUY publications are observed, the evaluator finalizes at exactly the horizon,
and statistics and monthly reports evolve identically. Nothing in this module
changes evaluation, finalization, or statistics semantics; recovery invents no
outcome rule of its own.

Strictly downstream and offline (LOCK 3)
----------------------------------------

No clock, file, socket, network, scheduler, run loop, delivery, monitoring,
fleet, or database access: recovery consumes values (frames and a snapshot)
and returns values. Persistence is the *caller's* concern — the Phase 26D
store loads the expected snapshot and hands it in; this module never imports
``smcsignal.persistence`` (which imports analytics: the dependency direction
stays ``persistence -> analytics``). One snapshot plus one frame history
describe one series; composing multiple series is the caller's orchestration
and does not exist here. This is not execution, a backtest, or advice.
"""

from __future__ import annotations

from collections.abc import Iterable

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.signal_engine.models import SignalSnapshot

from .ledger import LedgerSnapshot, snapshot_ledger
from .lifecycle import SignalOutcomeLifecycle
from .observer import AnalyticsObserver


class RecoveredLedger:
    """A verified live continuation of one persisted analytics ledger.

    Binds exactly three facts: the live Phase 26B ``SignalOutcomeLifecycle``
    to continue, the Phase 26C ``LedgerSnapshot`` it was verified against, and
    the number of historical frames replayed during verification. The holder
    is self-verifying: construction requires the lifecycle's current Phase 26C
    snapshot to equal the verified snapshot exactly, so an unverified pair can
    never be presented as recovered. The holder itself exposes no mutation —
    continuation happens through the real 26B lifecycle API only.
    """

    def __init__(
        self,
        lifecycle: SignalOutcomeLifecycle,
        verified_snapshot: LedgerSnapshot,
        frames_verified: int,
    ) -> None:
        if not isinstance(lifecycle, SignalOutcomeLifecycle):
            raise AnalysisInputError("recovered ledger requires a SignalOutcomeLifecycle")
        if not isinstance(verified_snapshot, LedgerSnapshot):
            raise AnalysisInputError("recovered ledger requires a Phase 26C LedgerSnapshot")
        if type(frames_verified) is not int or frames_verified < 0:
            raise AnalysisInputError("frames_verified must be a nonnegative integer")
        if snapshot_ledger(lifecycle) != verified_snapshot:
            raise AnalysisInputError(
                "a recovered ledger must be verified: the lifecycle's Phase 26C snapshot must "
                "equal the verified snapshot exactly"
            )
        self._lifecycle = lifecycle
        self._verified_snapshot = verified_snapshot
        self._frames_verified = frames_verified

    @property
    def lifecycle(self) -> SignalOutcomeLifecycle:
        """The verified live lifecycle; feed it new frames to continue."""

        return self._lifecycle

    @property
    def verified_snapshot(self) -> LedgerSnapshot:
        """The Phase 26C snapshot the reconstruction was verified against."""

        return self._verified_snapshot

    @property
    def frames_verified(self) -> int:
        """How many historical frames the replay covered and verified."""

        return self._frames_verified


def recover_lifecycle(
    frames: Iterable[SignalSnapshot],
    expected: LedgerSnapshot,
) -> RecoveredLedger:
    """Verified replay-based restart of one series' analytics lifecycle.

    Builds a fresh Phase 26B lifecycle under ``expected.settings``, replays
    ``frames`` — the series' complete historical ``SignalSnapshot`` sequence,
    regenerated through the real Phase 3-17 chain — through the unchanged 26B
    machinery, then projects the reconstruction through Phase 26C and requires
    exact equality with ``expected``. On equality the live lifecycle is
    returned inside a :class:`RecoveredLedger` ready for continuation; on any
    difference the call raises ``AnalysisInputError`` and returns nothing.

    The configuration is derived from the expected snapshot — never supplied
    separately — so a recovered lifecycle can never run under a configuration
    other than the one the snapshot records. The expected snapshot is frozen
    and is never mutated. Deterministic: identical inputs produce identical
    recoveries; repeated recovery of the same pair agrees exactly.
    """

    if not isinstance(expected, LedgerSnapshot):
        raise AnalysisInputError("recovery requires the Phase 26C LedgerSnapshot to verify against")
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of SignalSnapshot") from exc

    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(expected.settings))
    count = 0
    for frame in iterator:
        lifecycle.update(frame)
        count += 1

    reconstructed = snapshot_ledger(lifecycle)
    if reconstructed != expected:
        raise AnalysisInputError(
            "recovery rejected: the reconstructed ledger does not equal the expected snapshot "
            f"(reconstructed observations={len(reconstructed.observations)}, "
            f"finalized={len(reconstructed.finalized)}; "
            f"expected observations={len(expected.observations)}, "
            f"finalized={len(expected.finalized)}); no partially recovered lifecycle is returned"
        )
    return RecoveredLedger(lifecycle=lifecycle, verified_snapshot=expected, frames_verified=count)
