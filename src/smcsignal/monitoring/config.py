"""Strict, offline monitoring configuration: observation thresholds only.

This table can change only *what the monitor notices*. It cannot reach signal
generation, SMC/ICT thresholds, indicators, halal classification, eligibility,
risk, governance, or delivery routing and content: nothing here is consumed by
any producer, because monitoring is a downstream observer.

There is intentionally no alerting key and no secret material: no bot token, no
chat id, no destination, no endpoint. Unknown or missing keys, and unsafe
values, are rejected on load. The loader never reads the environment, never
touches the network, and never writes anything.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from decimal import Decimal, DecimalException
from pathlib import Path

from smcsignal.monitoring.errors import MonitoringConfigurationError

DEFAULT_STALE_AFTER_INTERVALS = 3
DEFAULT_GAP_TOLERANCE_INTERVALS = 0
DEFAULT_REPEATED_FAILURE_THRESHOLD = 3
DEFAULT_MAX_EVENTS_PER_RUN = 10_000

_TABLE = "monitoring"


def _integer(value: object, name: str, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise MonitoringConfigurationError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True, slots=True)
class MonitoringConfig:
    """Frozen observation thresholds.

    Defaults are inert: monitoring is opt-in and observation changes no
    behaviour anywhere in the pipeline.
    """

    enabled: bool = False
    stale_after_intervals: int = DEFAULT_STALE_AFTER_INTERVALS
    gap_tolerance_intervals: int = DEFAULT_GAP_TOLERANCE_INTERVALS
    repeated_failure_threshold: int = DEFAULT_REPEATED_FAILURE_THRESHOLD
    distribution_shift_low: Decimal = Decimal("0")
    distribution_shift_high: Decimal = Decimal("1")
    max_events_per_run: int = DEFAULT_MAX_EVENTS_PER_RUN

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise MonitoringConfigurationError("enabled must be a boolean")
        _integer(self.stale_after_intervals, "stale_after_intervals", minimum=1)
        _integer(self.gap_tolerance_intervals, "gap_tolerance_intervals", minimum=0)
        _integer(self.repeated_failure_threshold, "repeated_failure_threshold", minimum=1)
        _integer(self.max_events_per_run, "max_events_per_run", minimum=1)
        low = self.distribution_shift_low
        high = self.distribution_shift_high
        if not isinstance(low, Decimal) or not low.is_finite():
            raise MonitoringConfigurationError("distribution_shift_low must be a finite Decimal")
        if not isinstance(high, Decimal) or not high.is_finite():
            raise MonitoringConfigurationError("distribution_shift_high must be a finite Decimal")
        if not Decimal(0) <= low < high <= Decimal(1):
            raise MonitoringConfigurationError(
                "distribution_shift_low and distribution_shift_high"
                " must satisfy 0 <= low < high <= 1"
            )


def _decimal(raw: object, name: str) -> Decimal:
    """Parse a quoted decimal string; a TOML float is never accepted."""
    if not isinstance(raw, str):
        raise MonitoringConfigurationError(f"{name} must be a quoted decimal string")
    try:
        return Decimal(raw)
    except DecimalException as exc:
        raise MonitoringConfigurationError(f"invalid {name} decimal") from exc


def load_monitoring_config(path: str | Path) -> MonitoringConfig:
    """Load the exact ``[monitoring]`` table; unknown or missing keys are rejected."""
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get(_TABLE)
    except (OSError, ValueError, TypeError) as exc:
        raise MonitoringConfigurationError(f"cannot load monitoring configuration: {exc}") from exc
    names = {field.name for field in fields(MonitoringConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise MonitoringConfigurationError(
            "[monitoring] must contain exactly: " + ", ".join(sorted(names))
        )
    return MonitoringConfig(
        enabled=table["enabled"],
        stale_after_intervals=table["stale_after_intervals"],
        gap_tolerance_intervals=table["gap_tolerance_intervals"],
        repeated_failure_threshold=table["repeated_failure_threshold"],
        distribution_shift_low=_decimal(table["distribution_shift_low"], "distribution_shift_low"),
        distribution_shift_high=_decimal(
            table["distribution_shift_high"], "distribution_shift_high"
        ),
        max_events_per_run=table["max_events_per_run"],
    )
