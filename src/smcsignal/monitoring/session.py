"""One caller-scoped monitored run: begin, observe, end.

A session is the only stateful part of the monitoring layer, and it is stateful
on purpose: it owns a run's health map and counters so a report can be assembled
deterministically from what the run actually observed. Because there is no
daemon, a run is explicit - ``begin`` starts it, observations are recorded
against it, and ``end`` closes it exactly once.

Three rules govern recording:

* **An observation can only make health worse.** Every recorded condition is
  applied through the pure transition. Nothing here can improve a component's
  health, with one deliberate exception: a run that completed settles its own
  run-lifecycle component, because that is a statement about the run itself
  rather than about the data it observed.
* **Nothing a monitor does can lose an observation.** The session is the
  authority. A monitor that raises is contained, counted, and recorded as a
  self-failure instead of being allowed to propagate into a production call path,
  and health still reflects the condition that was observed.
* **Health reflects every condition seen, retained or not.** The retention cap
  bounds the event log, not the truth: a dropped event is counted and explained,
  never forgotten.

No observer, transport, or producer is imported or invoked here. The session
consumes already-built monitoring values and returns monitoring values.
"""

from __future__ import annotations

from collections.abc import Callable

from smcsignal.monitoring.clock import Clock
from smcsignal.monitoring.config import MonitoringConfig
from smcsignal.monitoring.errors import MonitoringInputError
from smcsignal.monitoring.health import transition, unobserved
from smcsignal.monitoring.metrics import Metric, MetricSummary
from smcsignal.monitoring.models import (
    HealthCode,
    HealthEvent,
    HealthEventAggregate,
    HealthState,
    MonitoredComponent,
    build_health_event,
)
from smcsignal.monitoring.monitor import Monitor, require_metric
from smcsignal.monitoring.report import (
    MonitoredRun,
    MonitoringReport,
    advance_aggregate,
    aggregate_events,
    merge_aggregates,
)


class MonitoringSession:
    """A monitored run that records observations and produces one report."""

    __slots__ = (
        "_clock",
        "_config",
        "_dropped",
        "_ended",
        "_health",
        "_metrics",
        "_monitor",
        "_observed",
        "_quarantined",
        "_retained_events",
        "_run",
        "_self_failures",
    )

    def __init__(
        self,
        *,
        run: MonitoredRun,
        clock: Clock,
        config: MonitoringConfig,
        monitor: Monitor,
    ) -> None:
        if not isinstance(run, MonitoredRun):
            raise MonitoringInputError("run must be a MonitoredRun")
        if not isinstance(config, MonitoringConfig):
            raise MonitoringInputError("config must be a MonitoringConfig")
        if not isinstance(clock, Clock):
            raise MonitoringInputError("clock must satisfy the Clock protocol")
        for method in ("record", "record_metric"):
            if not callable(getattr(monitor, method, None)):
                raise MonitoringInputError(f"monitor must provide {method}()")
        self._run = run
        self._clock = clock
        self._config = config
        self._monitor = monitor
        self._health = unobserved()
        self._quarantined: dict[str, HealthEventAggregate] = {}
        self._retained_events: list[HealthEvent] = []
        self._metrics: list[Metric] = []
        self._observed = 0
        self._dropped = 0
        self._self_failures = 0
        self._ended = False

    @classmethod
    def begin(
        cls,
        *,
        clock: Clock,
        config: MonitoringConfig,
        monitor: Monitor,
        label: str | None = None,
    ) -> MonitoringSession:
        """Start a run and record its ``RUN_STARTED`` condition."""
        session = cls(
            run=MonitoredRun.begin(clock=clock, label=label),
            clock=clock,
            config=config,
            monitor=monitor,
        )
        session.record(
            build_health_event(
                component=MonitoredComponent.RUN_LIFECYCLE,
                code=HealthCode.RUN_STARTED,
                observed_at=session._run.started_at,
                run_id=session._run.run_id,
            )
        )
        return session

    @property
    def run(self) -> MonitoredRun:
        """The run this session is recording."""
        return self._run

    @property
    def ended(self) -> bool:
        """Whether the run has been closed."""
        return self._ended

    @property
    def observed_events(self) -> int:
        """How many conditions have been observed."""
        return self._observed

    @property
    def dropped_observations(self) -> int:
        """How many conditions were observed but not retained."""
        return self._dropped

    @property
    def self_failures(self) -> int:
        """How many monitor calls failed and were contained."""
        return self._self_failures

    def health(self, component: MonitoredComponent) -> HealthState:
        """Return one component's current health state."""
        if not isinstance(component, MonitoredComponent):
            raise MonitoringInputError("health requires a MonitoredComponent")
        return self._health[component]

    def health_map(self) -> dict[MonitoredComponent, HealthState]:
        """Return a copy of the current, complete component health map."""
        return dict(self._health)

    def record(self, event: HealthEvent) -> None:
        """Observe one condition. Health always reflects it, retained or not."""
        self._require_open()
        if not isinstance(event, HealthEvent):
            raise MonitoringInputError("record requires a HealthEvent")
        self._observed += 1
        self._health[event.component] = transition(self._health[event.component], event)
        if len(self._retained_events) >= self._config.max_events_per_run:
            self._dropped += 1
            self._quarantine(HealthCode.MONITOR_OBSERVATION_DROPPED)
            return
        # The session is the authority: it keeps the observation whether or not
        # the destination can carry it.
        self._retained_events.append(event)
        self._contain(lambda: self._monitor.record(event))

    def record_metric(self, metric: Metric) -> None:
        """Keep one metric for the report and forward it, containing any failure."""
        self._require_open()
        stored = require_metric(metric)
        self._metrics.append(stored)
        self._contain(lambda: self._monitor.record_metric(stored))

    def end(self, *, failed: bool = False) -> MonitoringReport:
        """Close the run exactly once and return its immutable report."""
        self._require_open()
        if type(failed) is not bool:
            raise MonitoringInputError("failed must be a boolean")
        ended_at = self._clock.wall_clock()
        if ended_at < self._run.started_at:
            raise MonitoringInputError("the clock reports an instant before the run start")
        self.record(
            build_health_event(
                component=MonitoredComponent.RUN_LIFECYCLE,
                code=HealthCode.RUN_FAILED if failed else HealthCode.RUN_COMPLETED,
                observed_at=ended_at,
                run_id=self._run.run_id,
            )
        )
        self._ended = True
        if not failed:
            # The run finished, so its own lifecycle is healthy. Every other
            # component keeps whatever the observations said: clearing those is a
            # later, explicitly approved policy and is not decided here.
            self._health[MonitoredComponent.RUN_LIFECYCLE] = HealthState.HEALTHY
        return MonitoringReport(
            run=self._run,
            ended_at=ended_at,
            failed=failed,
            health=tuple(sorted(self._health.items(), key=lambda pair: pair[0].value)),
            aggregates=merge_aggregates(
                aggregate_events(self._retained_events),
                tuple(self._quarantined[identity] for identity in sorted(self._quarantined)),
            ),
            metrics=MetricSummary.of(*self._metrics),
            observed_events=self._observed,
            dropped_observations=self._dropped,
            self_failures=self._self_failures,
        )

    def _require_open(self) -> None:
        if self._ended:
            raise MonitoringInputError("the run has ended and may not be recorded to")

    def _contain(self, call: Callable[[], None]) -> None:
        """Run a monitor call, containing any failure as a monitoring defect.

        The failure is counted and recorded in the quarantine rather than raised:
        a broken downstream monitor must never interrupt a production call path,
        and must never be silent either.
        """
        try:
            call()
        except Exception:
            self._self_failures += 1
            self._quarantine(HealthCode.MONITOR_INTERNAL_FAILURE)

    def _quarantine(self, code: HealthCode) -> None:
        """Record a condition the monitor could not carry, aggregated and bounded.

        Only monitoring's own defect codes reach this channel, and they collapse by
        content identity, so the quarantine is bounded by the number of distinct
        codes rather than by how often a monitor failed.
        """
        event = build_health_event(
            component=MonitoredComponent.MONITORING,
            code=code,
            observed_at=self._clock.wall_clock(),
            run_id=self._run.run_id,
        )
        self._health[event.component] = transition(self._health[event.component], event)
        existing = self._quarantined.get(event.event_id)
        if existing is None:
            self._quarantined[event.event_id] = HealthEventAggregate.from_event(event)
        else:
            self._quarantined[event.event_id] = advance_aggregate(existing, event.observed_at)
