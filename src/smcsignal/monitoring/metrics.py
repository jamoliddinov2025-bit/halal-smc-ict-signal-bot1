"""Immutable metric shapes: exact counts, exact durations, ``Decimal`` ratios.

No float takes part in any arithmetic here. Counts are ``int``, durations are
``timedelta``, and every ratio or average is a ``Decimal`` evaluated under an
explicit precision policy, so a metric never varies with the ambient decimal
context and never depends on platform float formatting. Durations are converted
to ``Decimal`` milliseconds by exact scaling rather than by division.

Metrics are observations only. A metric can be incremented and read; it exposes
no way to influence a producer, and nothing upstream imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from smcsignal.monitoring.errors import MonitoringInputError
from smcsignal.monitoring.models import require_utc_timestamp

_ZERO_DURATION = timedelta(0)
_MICROSECONDS_PER_SECOND = 1_000_000
_SECONDS_PER_DAY = 86_400

# Applied explicitly on every division so the result is independent of whatever
# the process-wide decimal context happens to be.
_DECIMAL_PRECISION = 28


def _require_name(value: object, kind: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MonitoringInputError(f"{kind} name must be a nonempty, trimmed string")
    return value


def _require_count(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise MonitoringInputError(f"{name} must be a nonnegative integer")
    return value


def _require_duration(value: object, name: str) -> timedelta:
    if not isinstance(value, timedelta):
        raise MonitoringInputError(f"{name} must be a timedelta")
    if value < _ZERO_DURATION:
        raise MonitoringInputError(f"{name} must not be negative")
    return value


def _microseconds(value: timedelta) -> int:
    return (value.days * _SECONDS_PER_DAY + value.seconds) * _MICROSECONDS_PER_SECOND + (
        value.microseconds
    )


def _milliseconds(value: timedelta) -> Decimal:
    """Exact milliseconds as a ``Decimal``; scaling, never division."""
    return Decimal(f"{_microseconds(value)}e-3")


@dataclass(frozen=True, slots=True)
class CounterMetric:
    """A monotonically nondecreasing count. Exact, integral, never a float."""

    name: str
    total: int = 0

    def __post_init__(self) -> None:
        _require_name(self.name, "counter")
        _require_count(self.total, "total")

    def increment(self, amount: int = 1) -> CounterMetric:
        """Return a new counter increased by ``amount``; this one is unchanged."""
        if type(amount) is not int or amount < 1:
            raise MonitoringInputError("amount must be a positive integer")
        return replace(self, total=self.total + amount)

    def to_record(self) -> dict[str, object]:
        return {"kind": "counter", "name": self.name, "total": self.total}


@dataclass(frozen=True, slots=True)
class DurationMetric:
    """Exact duration aggregate. An empty metric reports nothing, not zero."""

    name: str
    count: int = 0
    total: timedelta = _ZERO_DURATION
    minimum: timedelta = _ZERO_DURATION
    maximum: timedelta = _ZERO_DURATION

    def __post_init__(self) -> None:
        _require_name(self.name, "duration")
        _require_count(self.count, "count")
        total = _require_duration(self.total, "total")
        minimum = _require_duration(self.minimum, "minimum")
        maximum = _require_duration(self.maximum, "maximum")
        if self.count == 0:
            empty = total == minimum == maximum == _ZERO_DURATION
            if not empty:
                raise MonitoringInputError("an empty duration metric must be all zero")
            return
        if minimum > maximum:
            raise MonitoringInputError("minimum must not exceed maximum")
        if minimum > total or maximum > total:
            raise MonitoringInputError("total must be at least the minimum and the maximum")

    def record(self, duration: timedelta) -> DurationMetric:
        """Return a new metric including one more measurement."""
        measured = _require_duration(duration, "duration")
        if self.count == 0:
            return replace(self, count=1, total=measured, minimum=measured, maximum=measured)
        return replace(
            self,
            count=self.count + 1,
            total=self.total + measured,
            minimum=self.minimum if self.minimum < measured else measured,
            maximum=self.maximum if self.maximum > measured else measured,
        )

    @property
    def average(self) -> Decimal | None:
        """Exact mean duration in microseconds, or ``None`` when nothing was measured."""
        if self.count == 0:
            return None
        with localcontext() as context:
            context.prec = _DECIMAL_PRECISION
            return Decimal(_microseconds(self.total)) / Decimal(self.count)

    def to_record(self) -> dict[str, object]:
        return {
            "kind": "duration",
            "name": self.name,
            "count": self.count,
            "total_milliseconds": _milliseconds(self.total),
            "minimum_milliseconds": _milliseconds(self.minimum),
            "maximum_milliseconds": _milliseconds(self.maximum),
        }


@dataclass(frozen=True, slots=True)
class RatioMetric:
    """An exact ratio, computed on read. A zero denominator yields ``None``."""

    name: str
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        _require_name(self.name, "ratio")
        _require_count(self.numerator, "numerator")
        _require_count(self.denominator, "denominator")

    @property
    def value(self) -> Decimal | None:
        """The exact ratio, or ``None`` when the denominator is zero.

        ``None`` is returned rather than zero or an infinity: nothing was
        observed, and reporting a number would invent one.
        """
        if self.denominator == 0:
            return None
        with localcontext() as context:
            context.prec = _DECIMAL_PRECISION
            return Decimal(self.numerator) / Decimal(self.denominator)

    def to_record(self) -> dict[str, object]:
        return {
            "kind": "ratio",
            "name": self.name,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class GaugeMetric:
    """A single latest reading with the instant it was taken."""

    name: str
    value: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_name(self.name, "gauge")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise MonitoringInputError("gauge value must be a finite Decimal")
        require_utc_timestamp(self.observed_at, "observed_at")

    def to_record(self) -> dict[str, object]:
        return {
            "kind": "gauge",
            "name": self.name,
            "value": self.value,
            "observed_at": self.observed_at,
        }


Metric = CounterMetric | DurationMetric | RatioMetric | GaugeMetric

_METRIC_TYPES = (CounterMetric, DurationMetric, RatioMetric, GaugeMetric)


@dataclass(frozen=True, slots=True)
class MetricSummary:
    """A frozen, name-sorted, duplicate-free container of metrics."""

    entries: tuple[Metric, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise MonitoringInputError("entries must be a tuple of metrics")
        names: list[str] = []
        for entry in self.entries:
            if not isinstance(entry, _METRIC_TYPES):
                raise MonitoringInputError("entries must contain only metric values")
            names.append(entry.name)
        if len(set(names)) != len(names):
            raise MonitoringInputError("metric names must be unique")
        if tuple(sorted(names)) != tuple(names):
            raise MonitoringInputError("entries must be sorted by name")

    @classmethod
    def of(cls, *metrics: Metric) -> MetricSummary:
        """Build a summary, sorting by name so the output is deterministic."""
        return cls(entries=tuple(sorted(metrics, key=lambda metric: metric.name)))

    def get(self, name: str) -> Metric | None:
        """Return the metric with this name, or ``None``."""
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def to_record(self) -> dict[str, object]:
        return {"entries": [entry.to_record() for entry in self.entries]}
