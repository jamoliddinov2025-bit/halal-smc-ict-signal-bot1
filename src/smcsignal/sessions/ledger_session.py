"""Phase 26G: the single-series ledger session — open-or-recover and continue.

Every primitive of the analytics arc exists and is frozen: the Phase 26B
lifecycle observes and finalizes, Phase 26C snapshots and restores canonical
bytes, Phase 26D stores them durably, and Phase 26E proves replay-based
recovery. What no frozen phase composes is the operational unit itself: given
a store, a key, an outcome configuration, and the series' regenerated frame
history — open the series' ledger, verified, ready to continue. That unit is
the ``LedgerSession``.

Explicit configuration, never defaulted (LOCK 1)
------------------------------------------------

``open_ledger_session(store, key, config, history)`` requires the caller's
``OutcomeTrackingConfig`` explicitly. Configuration is never defaulted,
inferred, replaced, or overridden: a missing store entry creates a fresh
lifecycle under exactly the supplied configuration, so
``fresh(config, history)`` equals the uninterrupted lifecycle over the same
inputs by construction. When a stored snapshot exists, its ``settings`` must
equal the supplied configuration before any replay — the stored settings act
as a verification constraint on the caller's single declared configuration,
never as a competing source; a mismatch refuses atomically.

Verified open (LOCK 2)
----------------------

Recovery rebuilds a fresh Phase 26B lifecycle under the declared
configuration and replays the supplied history through the unchanged 26B
machinery. The observer ledger is strictly append-only, so the observation
and finalization counts are monotone; reaching the stored snapshot's counts
merely *triggers* the candidate comparison — authorization is always complete
``LedgerSnapshot`` equality (settings, configuration hash, every observation
and final, content-addressed identity). Counts alone are never proof: a wrong
history with coincidentally similar counts fails the full comparison, and a
wrong configuration cannot even start the replay. ``store.save()`` is never
called before successful verification, so a failed open leaves the stored
bytes completely unchanged.

Plateau semantics of ``frames_verified`` (LOCK 3)
-------------------------------------------------

Non-BUY frames leave the ledger untouched, so the same complete snapshot can
legitimately correspond to several consecutive frame boundaries — the
original physical persist boundary is not uniquely identifiable from the
snapshot, and Phase 26C is deliberately not modified to make it so.
``frames_verified`` therefore documents exactly one thing: the number of
supplied history frames actually replayed during the verified reconstruction
at open. It never claims to identify the persisted snapshot's original frame
boundary.

Session discipline (LOCK 4)
---------------------------

``update()`` delegates to the real Phase 26B lifecycle and performs no
persistence IO; ``persist()`` explicitly snapshots and saves. A successful
open ends with one approved open-time persistence write, so a session is
always durable immediately after open. The session owns no frame source
(``smcsignal.series`` is composed by the caller), no second ledger or outcome
model, no state machine beyond the live lifecycle plus immutable provenance
facts, and no clock, file, network, scheduler, concurrency, fleet, delivery,
or monitoring capability — its only IO is the Phase 26D store it was given.
One session describes one series; multiple series mean multiple sessions
composed by the caller. This is not execution, a backtest, or advice.
"""

from __future__ import annotations

from collections.abc import Iterable

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import OutcomeTrackingConfig
from smcsignal.analysis.signal_engine.models import SignalSnapshot
from smcsignal.analytics import (
    AnalyticsObserver,
    LedgerSnapshot,
    LifecycleStep,
    SignalOutcomeLifecycle,
    snapshot_ledger,
)
from smcsignal.persistence import LedgerStore


class LedgerSession:
    """One live, verified single-series analytics ledger session.

    The session is the Phase 26B lifecycle plus the durable identity it was
    opened under (store, key, configuration) and immutable provenance facts
    from the open itself: ``recovered_from`` is the stored snapshot the
    session was verified against (``None`` for a fresh bootstrap), and
    ``frames_verified`` counts the supplied history frames actually replayed
    during the verified reconstruction at open — never the persisted
    snapshot's original physical frame boundary, which plateau frames make
    non-unique. The session adds no outcome rule, no statistic, and no state
    machine: continuation happens through the real 26B lifecycle via
    ``update()``, and durability happens only through ``persist()``.
    """

    def __init__(
        self,
        lifecycle: SignalOutcomeLifecycle,
        store: LedgerStore,
        key: str,
        config: OutcomeTrackingConfig,
        recovered_from: LedgerSnapshot | None,
        frames_verified: int,
    ) -> None:
        if not isinstance(lifecycle, SignalOutcomeLifecycle):
            raise AnalysisInputError("session requires a SignalOutcomeLifecycle")
        if not isinstance(config, OutcomeTrackingConfig):
            raise AnalysisInputError("session requires an explicit OutcomeTrackingConfig")
        if lifecycle.observer.config != config:
            raise AnalysisInputError("the session lifecycle must run under the declared config")
        if recovered_from is not None:
            if not isinstance(recovered_from, LedgerSnapshot):
                raise AnalysisInputError("recovered_from must be a LedgerSnapshot or None")
            if recovered_from.settings != config:
                raise AnalysisInputError("recovered_from must carry the declared configuration")
        if type(frames_verified) is not int or frames_verified < 0:
            raise AnalysisInputError("frames_verified must be a nonnegative integer")
        self._lifecycle = lifecycle
        self._store = store
        self._key = key
        self._config = config
        self._recovered_from = recovered_from
        self._frames_verified = frames_verified

    @property
    def lifecycle(self) -> SignalOutcomeLifecycle:
        """The live Phase 26B lifecycle; the single authoritative ledger."""

        return self._lifecycle

    @property
    def store(self) -> LedgerStore:
        """The Phase 26D store the session persists through."""

        return self._store

    @property
    def key(self) -> str:
        """The store key of this series' ledger."""

        return self._key

    @property
    def config(self) -> OutcomeTrackingConfig:
        """The explicitly declared outcome configuration of the session."""

        return self._config

    @property
    def recovered_from(self) -> LedgerSnapshot | None:
        """The stored snapshot verified at open; None for a fresh bootstrap."""

        return self._recovered_from

    @property
    def frames_verified(self) -> int:
        """Supplied history frames replayed during the verified open.

        This counts the reconstruction replay only. It does not identify the
        persisted snapshot's original frame boundary: plateau frames (frames
        that mutate nothing) make several boundaries correspond to the same
        complete snapshot.
        """

        return self._frames_verified

    def update(self, frame: SignalSnapshot) -> LifecycleStep:
        """Consume one frame through the real 26B lifecycle; no persistence IO."""

        return self._lifecycle.update(frame)

    def persist(self) -> LedgerSnapshot:
        """Explicitly snapshot the live ledger and save it under the session key."""

        snapshot = snapshot_ledger(self._lifecycle)
        self._store.save(self._key, snapshot)
        return snapshot


def open_ledger_session(
    store: LedgerStore,
    key: str,
    config: OutcomeTrackingConfig,
    history: Iterable[SignalSnapshot],
) -> LedgerSession:
    """Open one series' ledger session: fresh bootstrap or verified recovery.

    Missing store entry: a fresh Phase 26B lifecycle is built under exactly
    the supplied configuration and fed the entire supplied history. Existing
    entry: the stored snapshot's settings must equal the supplied
    configuration (else the open refuses before any replay); a fresh
    lifecycle under that same configuration replays the history while the
    monotone ledger counts watch for the stored snapshot's counts, and the
    first count match triggers the one authorization check — complete
    ``LedgerSnapshot`` equality. On success the remaining history is fed as
    continuation; on any failure the open raises atomically and the store is
    never written. Either way, a successful open ends with one approved
    open-time persistence write and returns the live session.

    Deterministic: identical store contents, key, configuration, and history
    produce identical sessions. The history frames are caller-supplied values
    (regenerated through Phase 26F or produced by any other origin); this
    function owns no frame source and imports none.
    """

    if not isinstance(config, OutcomeTrackingConfig):
        raise AnalysisInputError(
            "open_ledger_session requires an explicit OutcomeTrackingConfig; configuration is "
            "never defaulted, inferred, replaced, or overridden"
        )
    try:
        iterator = iter(history)
    except TypeError as exc:
        raise AnalysisInputError("history must be an iterable of SignalSnapshot") from exc

    stored = store.load(key)
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(config))
    replayed = 0

    if stored is None:
        for frame in iterator:
            lifecycle.update(frame)
            replayed += 1
        recovered_from: LedgerSnapshot | None = None
    else:
        if stored.settings != config:
            raise AnalysisInputError(
                "the stored ledger was persisted under a different outcome configuration; "
                "recovery refuses to run the series under settings it does not declare"
            )
        verified = snapshot_ledger(lifecycle) == stored
        for frame in iterator:
            lifecycle.update(frame)
            replayed += 1
            if not verified and (
                len(lifecycle.observer.observations) == len(stored.observations)
                and len(lifecycle.finalized_outcomes) == len(stored.finalized)
            ):
                if snapshot_ledger(lifecycle) != stored:
                    raise AnalysisInputError(
                        "recovery verification failed: the stored snapshot does not equal the "
                        "replayed history at the candidate point; no session is opened and the "
                        "store is unchanged"
                    )
                verified = True
        if not verified:
            raise AnalysisInputError(
                "recovery verification failed: the supplied history never reproduced the stored "
                "snapshot; no session is opened and the store is unchanged"
            )
        recovered_from = stored

    snapshot = snapshot_ledger(lifecycle)
    store.save(key, snapshot)
    return LedgerSession(
        lifecycle=lifecycle,
        store=store,
        key=key,
        config=config,
        recovered_from=recovered_from,
        frames_verified=replayed,
    )
