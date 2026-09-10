"""Phase 26A downstream analytics observer over the real signal publication path.

Connection point
----------------

The real signal pipeline publishes where ``SignalEngineAnalyzer.update()``
returns a ``SignalSnapshot`` (Phase 17, ``smcsignal.analysis.signal_engine``):
a frame whose status is ``BUY_SIGNAL`` is a published spot BUY fact. Phase 26A
connects analytics at exactly that boundary, the same way the Phase 18 outcome
tracker and the Phase 24 delivery coordinator already consume those frames:
the observer reads the returned immutable snapshot and never reaches into the
engine. ``ObservedSignalEngine`` is the minimal adapter that composes a real
engine instance with the observer; it changes no Phase 17 code and returns
every frame unchanged.

Lifecycle and its documented limitation
---------------------------------------

Observing a published BUY signal opens the initial Phase 18 ``SignalOutcome``
version (status ``OPEN``) through the existing ``open_outcome()`` mapping — the
analytics layer invents no outcome model of its own. Only the real market
outcome evaluator may finalize: a finalized record accepted by
``record_finalized()`` must be a non-OPEN Phase 18 outcome, and the Phase 18
model invariants admit finals only when the evaluator saw ``horizon_bars``
later candles of the signal's own series and classified the exact sign of the
final difference. The current architecture has no live market feed after
publication, so an outcome stays ``OPEN`` until such an evaluation exists;
open outcomes never flush, never expire, and never contribute to win/loss
statistics. This is the honest, documented limitation of Phase 26A.

Transport state is never an outcome
-----------------------------------

``DeliveryState`` (Telegram delivery success/failure) is a transport fact, not
a trade result. This module does not import ``smcsignal.delivery`` at all, and
no API here accepts a delivery state, receipt, or outcome envelope: a
``DELIVERED`` message can never become a ``WIN`` (or any other outcome).

Guarantees: downstream-only, deterministic, immutable, observational. One
outcome per published signal (duplicate observations are idempotent and never
double-count), one finalized record per outcome id (a conflicting duplicate is
rejected), no clock, no file, no network, no mutation of any upstream record.
"""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.outcome_tracking import (
    OutcomeStatus,
    OutcomeTrackingConfig,
    SignalOutcome,
    aggregate,
)
from smcsignal.analysis.outcome_tracking.calculation import open_outcome
from smcsignal.analysis.outcome_tracking.evidence import configuration_artifact, outcome_identity
from smcsignal.analysis.signal_engine.analyzer import SignalEngineAnalyzer
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus

from .models import (
    MonthlyReport,
    MonthlySummary,
    SignalObservation,
    StrategyStats,
    strategy_stats,
)


class AnalyticsObserver:
    """Downstream-only observer of published spot BUY signals.

    The observer holds the publication ledger of one outcome configuration:
    every observed BUY signal opens exactly one Phase 18 outcome version with
    ``OPEN`` status, and only a real market-evaluated final version (accepted
    through :meth:`record_finalized`) can close it. Observation is idempotent
    per outcome identity, so replaying a published frame can never create a
    second outcome or count a signal twice. The observer reads snapshots; it
    never mutates one, never calls the engine, and never touches delivery.
    """

    def __init__(self, config: OutcomeTrackingConfig | None = None) -> None:
        if config is not None and not isinstance(config, OutcomeTrackingConfig):
            raise AnalysisConfigurationError("config must be OutcomeTrackingConfig")
        self._config = config if config is not None else OutcomeTrackingConfig()
        self._artifact = configuration_artifact(self._config)
        self._config_hash = sha256(self._artifact).hexdigest()
        self._observations: dict[str, SignalObservation] = {}
        self._signal_to_outcome: dict[str, str] = {}
        self._finalized: dict[str, SignalOutcome] = {}

    @property
    def config(self) -> OutcomeTrackingConfig:
        return self._config

    @property
    def configuration_artifact(self) -> bytes:
        return self._artifact

    @property
    def configuration_hash(self) -> str:
        return self._config_hash

    @property
    def observations(self) -> tuple[SignalObservation, ...]:
        """Read-only observation-order view of every publication observation."""

        return tuple(self._observations.values())

    @property
    def open_outcomes(self) -> tuple[SignalOutcome, ...]:
        """Observed outcomes not yet finalized by the market evaluator."""

        return tuple(
            observation.outcome
            for observation in self._observations.values()
            if observation.outcome_id not in self._finalized
        )

    @property
    def finalized_outcomes(self) -> tuple[SignalOutcome, ...]:
        """Market-evaluated final versions in observation order."""

        return tuple(
            self._finalized[observation.outcome_id]
            for observation in self._observations.values()
            if observation.outcome_id in self._finalized
        )

    def is_tracked(self, outcome_id: str) -> bool:
        if not isinstance(outcome_id, str) or not outcome_id.strip():
            raise AnalysisInputError("outcome_id must be a nonempty string")
        return outcome_id in self._observations

    def observe(self, frame: SignalSnapshot) -> SignalObservation | None:
        """Observe one real signal frame exactly where the engine published it.

        Non-BUY frames are not publications and produce no observation. A BUY
        frame opens its initial OPEN Phase 18 outcome; observing the same
        published signal again returns the existing observation unchanged and
        never creates a duplicate outcome or a second count.
        """

        if not isinstance(frame, SignalSnapshot):
            raise AnalysisInputError(
                "analytics observes existing SignalSnapshot frames from the real signal engine"
            )
        if frame.status is not SignalStatus.BUY_SIGNAL:
            return None
        identity = self._outcome_identity(frame)
        existing = self._observations.get(identity)
        if existing is not None:
            return existing
        record = open_outcome(frame, self._config, self._config_hash)
        observation = SignalObservation(outcome=record)
        self._observations[identity] = observation
        prior = self._signal_to_outcome.get(frame.signal_id)
        if prior is not None and prior != identity:
            raise AnalysisInputError("one published signal maps to exactly one outcome identity")
        self._signal_to_outcome[frame.signal_id] = identity
        return observation

    def observe_all(self, frames: Iterable[SignalSnapshot]) -> tuple[SignalObservation, ...]:
        """Observe a frame sequence in order; returns the publication observations."""

        try:
            iterator = iter(frames)
        except TypeError as exc:
            raise AnalysisInputError("frames must be an iterable of SignalSnapshot") from exc
        observations: list[SignalObservation] = []
        for frame in iterator:
            observation = self.observe(frame)
            if observation is not None:
                observations.append(observation)
        return tuple(observations)

    def record_finalized(self, record: SignalOutcome) -> bool:
        """Record the market evaluator's final version for an observed outcome.

        Only a non-OPEN Phase 18 outcome is a final; the Phase 18 model
        invariants already prove it carries real horizon finals classified by
        the exact sign of the final difference. The outcome must have been
        observed through the real publication path, the configuration must
        match, and a repeat of the identical final is an idempotent no-op.
        Returns True when the outcome transitioned from OPEN to finalized.
        """

        if not isinstance(record, SignalOutcome):
            raise AnalysisInputError(
                "record_finalized accepts only Phase 18 SignalOutcome records produced by the "
                "market outcome evaluator"
            )
        if record.status is OutcomeStatus.OPEN:
            raise AnalysisInputError(
                "an open outcome cannot be recorded as finalized; only the market outcome "
                "evaluator can transition OPEN to WIN, LOSS, or BREAKEVEN (FLAT)"
            )
        if record.settings != self._config:
            raise AnalysisInputError("finalized record must use the observer configuration")
        observed = self._observations.get(record.outcome_id)
        if observed is None:
            raise AnalysisInputError(
                "only outcomes observed at the real publication boundary can be finalized"
            )
        if record.signal_id != observed.signal_id:
            raise AnalysisInputError("finalized record must track the observed signal")
        existing = self._finalized.get(record.outcome_id)
        if existing is not None:
            if existing != record:
                raise AnalysisInputError(
                    "a conflicting duplicate finalization of one outcome is rejected"
                )
            return False
        self._finalized[record.outcome_id] = record
        return True

    @property
    def strategy_stats(self) -> StrategyStats:
        """Descriptive statistics over observed signals; finals only count once."""

        finalized = self.finalized_outcomes
        open_count = len(self._observations) - len(finalized)
        return strategy_stats(finalized, open_count=open_count)

    def monthly_report(self) -> MonthlyReport:
        """Chronological UTC-month report over the observed publication ledger."""

        by_month: dict[str, list[SignalOutcome]] = {}
        for observation in self._observations.values():
            current = self._finalized.get(observation.outcome_id, observation.outcome)
            key = current.reference.opened_at.strftime("%Y-%m")
            by_month.setdefault(key, []).append(current)
        months: list[MonthlySummary] = []
        for key in sorted(by_month):
            records = tuple(by_month[key])
            finalized = tuple(
                record for record in records if record.status is not OutcomeStatus.OPEN
            )
            open_count = len(records) - len(finalized)
            months.append(
                MonthlySummary(
                    month=key,
                    summary=aggregate(
                        finalized,
                        total_buy_signals=len(records),
                        open_count=open_count,
                    ),
                )
            )
        return MonthlyReport(months=tuple(months), totals=self.strategy_stats)

    def _outcome_identity(self, frame: SignalSnapshot) -> str:
        """Reuse the deterministic Phase 18 outcome identity; never a new scheme."""

        return outcome_identity(frame, self._config)


class ObservedSignalEngine:
    """Adapter composing the real Phase 17 engine with the analytics observer.

    Every ``update()`` first runs the unchanged real engine to completion and
    only then forwards the returned snapshot to the observer, so analytics can
    never alter signal generation, SMC/ICT logic, halal filtering, confidence,
    or publication. The returned frame is the engine's frame, unmodified.
    """

    def __init__(
        self,
        observer: AnalyticsObserver,
        engine: SignalEngineAnalyzer | None = None,
    ) -> None:
        if not isinstance(observer, AnalyticsObserver):
            raise AnalysisInputError("adapter requires an AnalyticsObserver")
        if engine is not None and not isinstance(engine, SignalEngineAnalyzer):
            raise AnalysisInputError("adapter wraps a real SignalEngineAnalyzer")
        self._observer = observer
        self._engine = engine if engine is not None else SignalEngineAnalyzer()

    @property
    def engine(self) -> SignalEngineAnalyzer:
        return self._engine

    @property
    def observer(self) -> AnalyticsObserver:
        return self._observer

    @property
    def latest(self) -> SignalSnapshot | None:
        return self._engine.latest

    @property
    def processed_count(self) -> int:
        return self._engine.processed_count

    def update(self, frame: object) -> SignalSnapshot:
        """Run the real engine, then observe its published result downstream."""

        snapshot = self._engine.update(frame)  # type: ignore[arg-type]
        self._observer.observe(snapshot)
        return snapshot
