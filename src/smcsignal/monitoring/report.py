"""The descriptor of one monitored run and the immutable outcome it produces.

This module holds the run identity and the report, because a report is keyed by
the run that produced it. ``MonitoredRun`` is a caller-scoped lifecycle value:
because there is no daemon, a run is whatever a caller explicitly begins and ends.

A report is a value, not a view. It carries the complete component health map,
the deduplicated condition aggregates, the metric summary, and the counters that
explain what monitoring did with what it was given. Both ``overall`` and
``report_id`` are derived from that content rather than stored, so a report can
never carry a verdict or an identity that disagrees with its own data.

Aggregation is deterministic and total: conditions are keyed by their content
identity, so a recurring condition collapses into one aggregate with a count and
a seen-range, and the result is sorted by identity. Assembly widens the seen-range
with ``min``/``max`` instead of requiring ascending instants, because building a
report must not fail on data monitoring merely observed; recognising a clock
problem belongs to the observer layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from smcsignal.monitoring.errors import MonitoringInputError
from smcsignal.monitoring.health import rollup
from smcsignal.monitoring.metrics import MetricSummary
from smcsignal.monitoring.models import (
    HealthEvent,
    HealthEventAggregate,
    HealthState,
    MonitoredComponent,
    require_utc_timestamp,
)
from smcsignal.monitoring.serialization import content_digest

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from smcsignal.monitoring.clock import Clock

_RUN_METHODOLOGY = "monitoring-run-v1"
_RUN_ID_PREFIX = "run:"

_REPORT_METHODOLOGY = "monitoring-report-v1"
_REPORT_ID_PREFIX = "monitoring-report:"

# An operator label is a short, inert tag. A conservative character set keeps a
# pasted token, chat id, or message excerpt out of a report.
LABEL_MAX_LENGTH = 64
_LABEL_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _require_label(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise MonitoringInputError("label must be a nonempty trimmed string or None")
    if len(value) > LABEL_MAX_LENGTH:
        raise MonitoringInputError(f"label must be at most {LABEL_MAX_LENGTH} characters")
    if not set(value) <= _LABEL_CHARACTERS:
        raise MonitoringInputError(
            "label may contain only letters, digits, dot, underscore, hyphen"
        )
    return value


def run_identity(*, started_at: datetime, label: str | None) -> str:
    """Return the deterministic content identity of a run."""
    require_utc_timestamp(started_at, "started_at")
    _require_label(label)
    return _RUN_ID_PREFIX + content_digest(
        {
            "methodology": _RUN_METHODOLOGY,
            "started_at": started_at,
            "label": label,
        }
    )


@dataclass(frozen=True, slots=True)
class MonitoredRun:
    """One caller-scoped run: its identity, its start instant, and its label."""

    run_id: str
    started_at: datetime
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.startswith(_RUN_ID_PREFIX):
            raise MonitoringInputError(f"run_id must carry the {_RUN_ID_PREFIX} prefix")
        if len(self.run_id) == len(_RUN_ID_PREFIX):
            raise MonitoringInputError("run_id must carry a content identity after the prefix")
        require_utc_timestamp(self.started_at, "started_at")
        _require_label(self.label)

    @classmethod
    def begin(cls, *, clock: Clock, label: str | None = None) -> MonitoredRun:
        """Start a run at the instant the injected clock reports."""
        started_at = clock.wall_clock()
        require_utc_timestamp(started_at, "started_at")
        return cls(
            run_id=run_identity(started_at=started_at, label=label),
            started_at=started_at,
            label=label,
        )

    def to_record(self) -> dict[str, object]:
        return {"run_id": self.run_id, "started_at": self.started_at, "label": self.label}


def advance_aggregate(
    aggregate: HealthEventAggregate, observed_at: datetime
) -> HealthEventAggregate:
    """Widen an aggregate by one occurrence, tolerating an out-of-order instant."""
    if not isinstance(aggregate, HealthEventAggregate):
        raise MonitoringInputError("advance_aggregate requires a HealthEventAggregate")
    require_utc_timestamp(observed_at, "observed_at")
    return HealthEventAggregate(
        event_id=aggregate.event_id,
        component=aggregate.component,
        severity=aggregate.severity,
        code=aggregate.code,
        count=aggregate.count + 1,
        first_seen=min(aggregate.first_seen, observed_at),
        last_seen=max(aggregate.last_seen, observed_at),
    )


def aggregate_events(events: Sequence[HealthEvent]) -> tuple[HealthEventAggregate, ...]:
    """Collapse conditions into aggregates sorted by content identity."""
    ordered: dict[str, HealthEventAggregate] = {}
    for event in events:
        if not isinstance(event, HealthEvent):
            raise MonitoringInputError("aggregate_events requires HealthEvent values")
        existing = ordered.get(event.event_id)
        if existing is None:
            ordered[event.event_id] = HealthEventAggregate.from_event(event)
        else:
            ordered[event.event_id] = advance_aggregate(existing, event.observed_at)
    return tuple(ordered[event_id] for event_id in sorted(ordered))


def merge_aggregates(
    *groups: tuple[HealthEventAggregate, ...],
) -> tuple[HealthEventAggregate, ...]:
    """Combine aggregate groups that share one identity, sorted by identity."""
    ordered: dict[str, HealthEventAggregate] = {}
    for group in groups:
        for aggregate in group:
            if not isinstance(aggregate, HealthEventAggregate):
                raise MonitoringInputError("merge_aggregates requires aggregates")
            existing = ordered.get(aggregate.event_id)
            if existing is None:
                ordered[aggregate.event_id] = aggregate
                continue
            condition = (existing.component, existing.severity, existing.code)
            if condition != (aggregate.component, aggregate.severity, aggregate.code):
                raise MonitoringInputError(
                    "aggregates sharing an identity must describe the same condition"
                )
            ordered[aggregate.event_id] = HealthEventAggregate(
                event_id=existing.event_id,
                component=existing.component,
                severity=existing.severity,
                code=existing.code,
                count=existing.count + aggregate.count,
                first_seen=min(existing.first_seen, aggregate.first_seen),
                last_seen=max(existing.last_seen, aggregate.last_seen),
            )
    return tuple(ordered[event_id] for event_id in sorted(ordered))


@dataclass(frozen=True, slots=True)
class MonitoringReport:
    """Frozen, deterministic outcome of one monitored run."""

    run: MonitoredRun
    ended_at: datetime
    failed: bool
    health: tuple[tuple[MonitoredComponent, HealthState], ...]
    aggregates: tuple[HealthEventAggregate, ...]
    metrics: MetricSummary
    observed_events: int
    dropped_observations: int
    self_failures: int

    def __post_init__(self) -> None:
        if not isinstance(self.run, MonitoredRun):
            raise MonitoringInputError("run must be a MonitoredRun")
        require_utc_timestamp(self.ended_at, "ended_at")
        if self.ended_at < self.run.started_at:
            raise MonitoringInputError("ended_at must not precede the run start")
        if type(self.failed) is not bool:
            raise MonitoringInputError("failed must be a boolean")
        if not isinstance(self.health, tuple):
            raise MonitoringInputError("health must be a tuple of component/state pairs")
        components: list[MonitoredComponent] = []
        for pair in self.health:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise MonitoringInputError("health must be a tuple of component/state pairs")
            component, state = pair
            if not isinstance(component, MonitoredComponent):
                raise MonitoringInputError("health keys must be MonitoredComponent values")
            if not isinstance(state, HealthState):
                raise MonitoringInputError("health values must be HealthState values")
            components.append(component)
        if set(components) != set(MonitoredComponent):
            raise MonitoringInputError("health must state every component exactly once")
        if components != sorted(components, key=lambda item: item.value):
            raise MonitoringInputError("health must be sorted by component")
        if not isinstance(self.aggregates, tuple):
            raise MonitoringInputError("aggregates must be a tuple")
        identities: list[str] = []
        for aggregate in self.aggregates:
            if not isinstance(aggregate, HealthEventAggregate):
                raise MonitoringInputError("aggregates must contain aggregates")
            identities.append(aggregate.event_id)
        if identities != sorted(identities):
            raise MonitoringInputError("aggregates must be sorted by event id")
        if len(set(identities)) != len(identities):
            raise MonitoringInputError("aggregate identities must be unique")
        if not isinstance(self.metrics, MetricSummary):
            raise MonitoringInputError("metrics must be a MetricSummary")
        for name in ("observed_events", "dropped_observations", "self_failures"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise MonitoringInputError(f"{name} must be a nonnegative integer")
        # Self-failures also count contained metric-forwarding failures, which are
        # not events, so only the drop count is bounded by the observed count.
        if self.dropped_observations > self.observed_events:
            raise MonitoringInputError("dropped observations must not exceed those observed")

    def health_map(self) -> dict[MonitoredComponent, HealthState]:
        """Return the component health map as a fresh mutable mapping."""
        return dict(self.health)

    def state(self, component: MonitoredComponent) -> HealthState:
        """Return one component's health state."""
        if not isinstance(component, MonitoredComponent):
            raise MonitoringInputError("state requires a MonitoredComponent")
        return self.health_map()[component]

    @property
    def overall(self) -> HealthState:
        """The worst-known state across every component; derived, never stored."""
        return rollup(self.health_map())

    def _payload(self) -> dict[str, object]:
        return {
            "methodology": _REPORT_METHODOLOGY,
            "run": self.run.to_record(),
            "ended_at": self.ended_at,
            "failed": self.failed,
            "health": [
                {"component": component.value, "state": state.value}
                for component, state in self.health
            ],
            "aggregates": [aggregate.to_record() for aggregate in self.aggregates],
            "metrics": self.metrics.to_record(),
            "observed_events": self.observed_events,
            "dropped_observations": self.dropped_observations,
            "self_failures": self.self_failures,
        }

    @property
    def report_id(self) -> str:
        """Deterministic digest of the whole report content, excluding the id."""
        return _REPORT_ID_PREFIX + content_digest(self._payload())

    def to_record(self) -> dict[str, object]:
        """Return the canonical record, including the derived identity."""
        return {**self._payload(), "report_id": self.report_id}
