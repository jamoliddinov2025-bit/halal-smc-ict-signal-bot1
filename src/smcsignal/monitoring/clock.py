"""Injected wall and monotonic clocks for the monitoring layer.

Monitoring never reads ambient time: every timestamp is supplied by a ``Clock``,
so a monitoring report is reproducible in tests and immune to a wall-clock jump.
Wall time is used only for human-facing attribution of an event; all durations
are measured with the monotonic clock, which cannot run backwards. ``SystemClock``
is the single place in this layer that reads the operating system's clocks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Protocol, runtime_checkable

from smcsignal.monitoring.errors import MonitoringInputError
from smcsignal.monitoring.models import require_utc_timestamp


def _require_finite_seconds(value: object, name: str) -> float:
    """Return ``value`` as a finite float or raise ``MonitoringInputError``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MonitoringInputError(f"{name} must be a real number")
    try:
        number = float(value)
    except OverflowError as exc:  # pragma: no cover - defensive for huge integers
        raise MonitoringInputError(f"{name} must be a finite real number") from exc
    if not isfinite(number):
        raise MonitoringInputError(f"{name} must be finite")
    return number


@runtime_checkable
class Clock(Protocol):
    """A pair of injected clocks: attribution time and measurement time."""

    def wall_clock(self) -> datetime:
        """Return the current timezone-aware instant used for ``observed_at``."""
        ...

    def monotonic(self) -> float:
        """Return a monotonically nondecreasing reading used for durations only."""
        ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Real clocks, for production use.

    This is the only place in the monitoring layer that reads ambient time. A
    production report therefore depends on real instants, while every test can
    substitute ``FixedClock`` and obtain byte-identical output.
    """

    def wall_clock(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass(frozen=True, slots=True)
class FixedClock:
    """Deterministic clock for tests: returns stored readings unchanged."""

    instant: datetime
    monotonic_seconds: float = 0.0

    def __post_init__(self) -> None:
        require_utc_timestamp(self.instant, "instant")
        _require_finite_seconds(self.monotonic_seconds, "monotonic_seconds")

    def wall_clock(self) -> datetime:
        return self.instant

    def monotonic(self) -> float:
        return self.monotonic_seconds

    def advance(self, *, seconds: float = 0.0, monotonic_seconds: float = 0.0) -> FixedClock:
        """Return a new clock advanced by the deltas, never mutating this one.

        Negative deltas are permitted so that a wall-clock step or a
        non-monotonic reading can be simulated; detecting and reporting those is
        the monitor's job, not the clock's.
        """
        _require_finite_seconds(seconds, "seconds")
        _require_finite_seconds(monotonic_seconds, "monotonic_seconds")
        return replace(
            self,
            instant=self.instant + timedelta(seconds=seconds),
            monotonic_seconds=self.monotonic_seconds + monotonic_seconds,
        )
