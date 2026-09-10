"""Phase 25 monitoring: a read-only, strictly downstream observer.

This package consumes records that other layers have already published - market
data batches and their validation reports, published signal snapshots, and
delivery outcomes - and returns monitoring values of its own: immutable events,
aggregates, and (in later sub-phases) health states, metrics, and reports.

It is a leaf. Nothing upstream imports it, no entry point accepts a producer to
mutate, and every return value is a monitoring value. It therefore cannot
generate, gate, veto, reinterpret, or modify a signal, a halal classification, an
eligibility decision, a risk decision, a Phase 23 governance decision, or a
delivery. Telegram remains downstream only: monitoring observes delivery
outcomes and never sends, retries, retargets, or reorders anything.

Phase 25B-1 ships the foundations only: errors, models, clocks, and
configuration. Observers, sessions, reports, metrics, and alerting arrive in
later sub-phases. The approved design is
``docs/phase25a-production-monitoring-reliability-design.md``.
"""

from smcsignal.monitoring.clock import Clock, FixedClock, SystemClock
from smcsignal.monitoring.config import (
    DEFAULT_GAP_TOLERANCE_INTERVALS,
    DEFAULT_MAX_EVENTS_PER_RUN,
    DEFAULT_REPEATED_FAILURE_THRESHOLD,
    DEFAULT_STALE_AFTER_INTERVALS,
    MonitoringConfig,
    load_monitoring_config,
)
from smcsignal.monitoring.errors import (
    MonitoringConfigurationError,
    MonitoringError,
    MonitoringInputError,
)
from smcsignal.monitoring.models import (
    SEVERITY_BY_CODE,
    HealthCode,
    HealthEvent,
    HealthEventAggregate,
    HealthState,
    MonitoredComponent,
    Severity,
    build_health_event,
    event_identity,
    require_detail,
    require_subject_id,
    require_utc_timestamp,
    severity_for,
)

__all__ = [
    "Clock",
    "DEFAULT_GAP_TOLERANCE_INTERVALS",
    "DEFAULT_MAX_EVENTS_PER_RUN",
    "DEFAULT_REPEATED_FAILURE_THRESHOLD",
    "DEFAULT_STALE_AFTER_INTERVALS",
    "FixedClock",
    "HealthCode",
    "HealthEvent",
    "HealthEventAggregate",
    "HealthState",
    "MonitoredComponent",
    "MonitoringConfig",
    "MonitoringConfigurationError",
    "MonitoringError",
    "MonitoringInputError",
    "SEVERITY_BY_CODE",
    "Severity",
    "SystemClock",
    "build_health_event",
    "event_identity",
    "load_monitoring_config",
    "require_detail",
    "require_subject_id",
    "require_utc_timestamp",
    "severity_for",
]
