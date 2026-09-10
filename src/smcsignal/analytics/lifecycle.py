"""Phase 26B: the deterministic outcome lifecycle over the real publication path.

``SignalOutcomeLifecycle`` is pure composition of three already-frozen pieces;
it owns no outcome arithmetic, no evaluation rule, and no statistic of its own:

1. ``OutcomeTrackingAnalyzer.update()`` — the unchanged Phase 18 market outcome
   evaluator — validates and consumes the frame, evaluates open outcomes
   against the newly closed candle, and finalizes at exactly the horizon by
   the exact sign of the final difference.
2. ``AnalyticsObserver.observe()`` records the publication fact at the Phase 17
   boundary and opens the initial OPEN Phase 18 outcome.
3. ``AnalyticsObserver.record_finalized()`` accepts each completed final into
   the single authoritative publication ledger, which feeds ``StrategyStats``
   and the UTC ``MonthlyReport`` through the frozen Phase 18 ``aggregate()``.

Per-frame order is pinned: evaluate, then observe, then forward finals. This
ordering is atomic — an out-of-order or replayed frame raises inside the frozen
Phase 18 evaluator before any ledger state changes, matching the Phase 18
"rejected atomically" discipline. Observe-before-finalize stays structural: a
BUY published at frame ``i`` can finalize no earlier than frame
``i + horizon_bars`` with ``horizon_bars >= 1``, so its publication fact is
always recorded in the frame it is published, strictly before any final for it
can exist. Finals reach the ledger exactly once: Phase 18 finalizes each
outcome exactly once at the horizon and never re-evaluates a final, and the
observer is idempotent per outcome identity.

Inherited invariants (enforced by the frozen Phase 18 evaluator, never
re-implemented here): one series per lifecycle, consecutive candle indices
starting at zero, chronological unique candles, availability that never
rewinds, and atomic rejection of any replayed frame. A lifecycle therefore
describes exactly one signal series; multiple series mean multiple lifecycles
composed by the caller — no fleet orchestration exists here. The lifecycle
requires a fresh observer and a fresh evaluator sharing one configuration, so
its ledger can never mix series or inherit untracked finals.

Strictly downstream and deterministic: no clock, file, network, scheduler, or
run loop; no delivery or ``DeliveryState`` reference anywhere; transport state
can never become a trade outcome. Open outcomes never flush, never expire, and
contribute only to open counts. This is not execution, a backtest, or advice.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import (
    OutcomeSnapshot,
    OutcomeTrackingAnalyzer,
    SignalOutcome,
)
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus

from .models import MonthlyReport, SignalObservation, StrategyStats
from .observer import AnalyticsObserver


@dataclass(frozen=True, slots=True)
class LifecycleStep:
    """One deterministic lifecycle frame: the publication observation plus the
    evaluator snapshot produced for the same consumed frame.

    Both parts are already immutable Phase 26A / Phase 18 records; the step only
    binds them and pins their consistency. ``observation`` is None exactly when
    the consumed frame was not a published BUY_SIGNAL.
    """

    observation: SignalObservation | None
    outcome_snapshot: OutcomeSnapshot

    def __post_init__(self) -> None:
        if self.observation is not None and not isinstance(self.observation, SignalObservation):
            raise AnalysisInputError("observation must be a SignalObservation or None")
        if not isinstance(self.outcome_snapshot, OutcomeSnapshot):
            raise AnalysisInputError("step requires the Phase 18 OutcomeSnapshot")
        upstream = self.outcome_snapshot.upstream
        if (self.observation is not None) is not (upstream.status is SignalStatus.BUY_SIGNAL):
            raise AnalysisInputError("a step observes a publication exactly on BUY_SIGNAL frames")
        if self.observation is None:
            return
        if self.observation.signal_id != upstream.signal_id:
            raise AnalysisInputError("step observation must track the consumed frame")
        if len(self.outcome_snapshot.created) != 1:
            raise AnalysisInputError("a BUY_SIGNAL frame creates exactly one tracker outcome")
        if self.observation.outcome != self.outcome_snapshot.created[0]:
            raise AnalysisInputError(
                "the observer and the evaluator must open the identical Phase 18 record"
            )


class SignalOutcomeLifecycle:
    """Publication observation, market evaluation, and finalization in one loop.

    The lifecycle consumes real ``SignalSnapshot`` frames of one series in
    order — exactly the frames the Phase 17 engine published — and keeps the
    observer's ledger as the single authoritative publication record. Both the
    observer and the evaluator must be fresh: a used tracker could carry finals
    for signals this observer never saw, and a pre-populated observer could mix
    another series into this lifecycle's ledger — either would break the
    single-series, observe-before-finalize guarantees.
    """

    def __init__(
        self,
        observer: AnalyticsObserver,
        tracker: OutcomeTrackingAnalyzer | None = None,
    ) -> None:
        if not isinstance(observer, AnalyticsObserver):
            raise AnalysisInputError("lifecycle requires an AnalyticsObserver")
        if observer.observations:
            raise AnalysisInputError(
                "lifecycle requires a fresh observer so its ledger covers exactly one series"
            )
        candidate = tracker if tracker is not None else OutcomeTrackingAnalyzer(observer.config)
        if not isinstance(candidate, OutcomeTrackingAnalyzer):
            raise AnalysisInputError("lifecycle requires a Phase 18 OutcomeTrackingAnalyzer")
        if candidate.config != observer.config:
            raise AnalysisInputError("observer and evaluator must share one OutcomeTrackingConfig")
        if candidate.processed_count != 0:
            raise AnalysisInputError(
                "lifecycle requires a fresh evaluator: a used tracker could carry finals for "
                "signals this observer never observed"
            )
        self._observer = observer
        self._tracker = candidate

    @property
    def observer(self) -> AnalyticsObserver:
        return self._observer

    @property
    def tracker(self) -> OutcomeTrackingAnalyzer:
        return self._tracker

    @property
    def latest(self) -> OutcomeSnapshot | None:
        return self._tracker.latest

    @property
    def processed_count(self) -> int:
        return self._tracker.processed_count

    @property
    def open_outcomes(self) -> tuple[SignalOutcome, ...]:
        """Observed outcomes not yet finalized by the market evaluator."""

        return self._observer.open_outcomes

    @property
    def finalized_outcomes(self) -> tuple[SignalOutcome, ...]:
        """Market-evaluated finals in observation order; each counted once."""

        return self._observer.finalized_outcomes

    @property
    def strategy_stats(self) -> StrategyStats:
        return self._observer.strategy_stats

    def monthly_report(self) -> MonthlyReport:
        return self._observer.monthly_report()

    def update(self, frame: SignalSnapshot) -> LifecycleStep:
        """Consume one real engine frame: evaluate, observe, then finalize.

        The evaluator runs first so any replayed or out-of-order frame raises
        inside the frozen Phase 18 machinery before the ledger changes; the
        publication fact is then recorded, and only then are this frame's
        completed finals forwarded to the ledger. Observe-before-finalize is
        structural: a final always belongs to an earlier published frame.
        """

        if not isinstance(frame, SignalSnapshot):
            raise AnalysisInputError(
                "lifecycle consumes existing SignalSnapshot frames from the real signal engine"
            )
        snapshot = self._tracker.update(frame)
        observation = self._observer.observe(frame)
        for record in snapshot.completed:
            self._observer.record_finalized(record)
        return LifecycleStep(observation=observation, outcome_snapshot=snapshot)


def run_lifecycle(
    frames: Iterable[SignalSnapshot],
    lifecycle: SignalOutcomeLifecycle | None = None,
) -> tuple[LifecycleStep, ...]:
    """Sequential batch run over one series; identical to streaming updates."""

    active = lifecycle if lifecycle is not None else SignalOutcomeLifecycle(AnalyticsObserver())
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of SignalSnapshot") from exc
    return tuple(active.update(frame) for frame in iterator)
