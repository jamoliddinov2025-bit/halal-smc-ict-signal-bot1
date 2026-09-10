"""Where monitoring observations go, and what happens when nothing is watching.

``Monitor`` is a write-only observation *destination*, mirroring the delivery
layer's sink precedent: it receives already-validated monitoring values and
stores or forwards them. It is deliberately a destination and not an authority -
the session owns the run, the health map, the event log, and the report - so a
monitor can fail, discard, or be absent without changing what was observed.

``NullMonitor`` is the no-op destination: every call returns without doing work,
so a call site can run with no downstream consumer at zero cost and zero
behaviour change. ``RecordingMonitor`` keeps observations in memory for
inspection, tests, and diagnostics; its readers are concrete conveniences and are
deliberately not part of the protocol, so no destination is obliged to remember
anything.

No monitor may alter the values it receives, and none may influence a producer:
the types accepted here are frozen monitoring values that carry no handle on the
signal, data, or delivery layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from smcsignal.monitoring.errors import MonitoringInputError
from smcsignal.monitoring.metrics import (
    CounterMetric,
    DurationMetric,
    GaugeMetric,
    Metric,
    RatioMetric,
)
from smcsignal.monitoring.models import HealthEvent

_METRIC_TYPES = (CounterMetric, DurationMetric, RatioMetric, GaugeMetric)


def require_metric(value: object) -> Metric:
    """Return ``value`` if it is a monitoring metric, else raise."""
    if not isinstance(value, _METRIC_TYPES):
        raise MonitoringInputError("a monitoring metric value is required")
    return value


@runtime_checkable
class Monitor(Protocol):
    """A write-only destination for validated monitoring observations."""

    def record(self, event: HealthEvent) -> None:
        """Accept one observed condition."""
        ...

    def record_metric(self, metric: Metric) -> None:
        """Accept one metric value."""
        ...


@dataclass(frozen=True, slots=True)
class NullMonitor:
    """A monitor that observes nothing and stores nothing.

    Every call is a no-op, so this is the destination to use when nothing is
    watching. It never raises, which is what makes it safe to place anywhere.
    """

    def record(self, event: HealthEvent) -> None:
        return None

    def record_metric(self, metric: Metric) -> None:
        return None


class RecordingMonitor:
    """An in-memory observation accumulator for inspection and tests.

    This is intentionally not a frozen dataclass: it is a collection being built,
    not a value. Every observation it stores is itself immutable, previously
    stored observations are never rewritten, and reads return tuples so a caller
    cannot mutate the accumulator through them.
    """

    __slots__ = ("_events", "_metrics")

    def __init__(self) -> None:
        self._events: list[HealthEvent] = []
        self._metrics: list[Metric] = []

    def record(self, event: HealthEvent) -> None:
        if not isinstance(event, HealthEvent):
            raise MonitoringInputError("record requires a HealthEvent")
        self._events.append(event)

    def record_metric(self, metric: Metric) -> None:
        self._metrics.append(require_metric(metric))

    def events(self) -> tuple[HealthEvent, ...]:
        return tuple(self._events)

    def metrics(self) -> tuple[Metric, ...]:
        return tuple(self._metrics)
