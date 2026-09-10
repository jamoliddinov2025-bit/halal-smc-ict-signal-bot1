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

Phase 25B builds the inert core first: errors, models, clocks, and configuration
(25B-1), then the pure health transitions, precedence rollup, and the metric
model layer (25B-2), then runs, sessions, reports, and the shared serialization
helpers (25B-3), and then the market-data observer (25B-4). The signal and
delivery observers, alerting, and any runtime wiring into a production pipeline
remain unimplemented, so nothing here is reachable from a running pipeline and
nothing here reads a clock, a file, or a network. The approved design is
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
from smcsignal.monitoring.health import (
    HEALTH_PRECEDENCE,
    INITIAL_HEALTH_STATE,
    clear,
    rank,
    rollup,
    transition,
    unobserved,
    worse_of,
)
from smcsignal.monitoring.metrics import (
    CounterMetric,
    DurationMetric,
    GaugeMetric,
    Metric,
    MetricSummary,
    RatioMetric,
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
from smcsignal.monitoring.monitor import (
    Monitor,
    NullMonitor,
    RecordingMonitor,
    require_metric,
)
from smcsignal.monitoring.observers import (
    MARKET_DATA_METRICS,
    DataObservation,
    MarketDataObserver,
    series_subject,
)
from smcsignal.monitoring.report import (
    LABEL_MAX_LENGTH,
    MonitoredRun,
    MonitoringReport,
    advance_aggregate,
    aggregate_events,
    merge_aggregates,
    run_identity,
)
from smcsignal.monitoring.serialization import (
    canonical_bytes,
    canonical_record,
    content_digest,
    require_serializable,
)
from smcsignal.monitoring.session import MonitoringSession

__all__ = [
    "Clock",
    "CounterMetric",
    "DEFAULT_GAP_TOLERANCE_INTERVALS",
    "DEFAULT_MAX_EVENTS_PER_RUN",
    "DEFAULT_REPEATED_FAILURE_THRESHOLD",
    "DEFAULT_STALE_AFTER_INTERVALS",
    "DataObservation",
    "DurationMetric",
    "FixedClock",
    "GaugeMetric",
    "HEALTH_PRECEDENCE",
    "HealthCode",
    "HealthEvent",
    "HealthEventAggregate",
    "HealthState",
    "INITIAL_HEALTH_STATE",
    "LABEL_MAX_LENGTH",
    "MARKET_DATA_METRICS",
    "MarketDataObserver",
    "Metric",
    "MetricSummary",
    "Monitor",
    "MonitoredComponent",
    "MonitoredRun",
    "MonitoringConfig",
    "MonitoringConfigurationError",
    "MonitoringError",
    "MonitoringInputError",
    "MonitoringReport",
    "MonitoringSession",
    "NullMonitor",
    "RatioMetric",
    "RecordingMonitor",
    "SEVERITY_BY_CODE",
    "Severity",
    "SystemClock",
    "advance_aggregate",
    "aggregate_events",
    "build_health_event",
    "canonical_bytes",
    "canonical_record",
    "clear",
    "content_digest",
    "event_identity",
    "load_monitoring_config",
    "merge_aggregates",
    "rank",
    "require_detail",
    "require_metric",
    "require_serializable",
    "require_subject_id",
    "require_utc_timestamp",
    "rollup",
    "run_identity",
    "series_subject",
    "severity_for",
    "transition",
    "unobserved",
    "worse_of",
]
