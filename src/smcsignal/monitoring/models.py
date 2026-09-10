"""Immutable monitoring facts: components, conditions, events, and aggregates.

Phase 25 monitoring is a read-only observer. It consumes records that other
layers have already published and returns monitoring values of its own. Nothing
here can generate, gate, veto, reinterpret, or modify a signal, a halal
classification, an eligibility decision, a risk decision, a governance decision,
or a delivery: every value is a frozen dataclass, every enum is closed, and no
constructor accepts an upstream object to mutate.

Conditions are identified by content. An event identity deliberately excludes
wall-clock time and the run it belongs to, so the same recurring condition
deduplicates instead of storming, while a different subject stays a different
event. Severity is a property of the condition code, never a caller choice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from smcsignal.analysis.liquidity.evidence import digest
from smcsignal.monitoring.errors import MonitoringInputError

_EVENT_METHODOLOGY = "monitoring-event-v1"

_EVENT_ID_PREFIX = "health:"
_RUN_ID_PREFIX = "run:"

# A subject is an identity-like value that must already be safe to store. A
# redacted destination is produced by the existing delivery redaction helper; a
# canonical identity prefix is used for non-secret subjects. Anything else - a
# raw chat id, a raw token, a bare number - is rejected so that secret material
# cannot reach a monitoring record through the subject field.
_REDACTED_MARKER = "<redacted>"
_SUBJECT_ID_PREFIXES = (
    "chart:",
    "delivery:",
    "destination:",
    "health:",
    "message:",
    "run:",
    "series:",
    "signal:",
)

# Detail is a compact canonical fact, never a place to dump a payload. These key
# fragments are refused outright because their values are the ones that leak
# secrets or user content.
_FORBIDDEN_DETAIL_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "caption",
    "chat",
    "password",
    "secret",
    "token",
    "url",
    "webhook",
)
_MAX_DETAIL_VALUE_LENGTH = 200


class MonitoredComponent(StrEnum):
    """The closed set of components a monitor may report on."""

    MARKET_DATA = "market_data"
    DATA_INTEGRITY = "data_integrity"
    SIGNAL_ENGINE = "signal_engine"
    DELIVERY = "delivery"
    TELEGRAM_TRANSPORT = "telegram_transport"
    MONITORING = "monitoring"
    GOVERNANCE_OBSERVER = "governance_observer"
    RUN_LIFECYCLE = "run_lifecycle"


class Severity(StrEnum):
    """How loudly a condition should be surfaced to an operator."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class HealthState(StrEnum):
    """The closed health vocabulary.

    ``UNKNOWN`` is not a placeholder: it means "not observed, or not observed
    recently enough to trust". It always outranks ``HEALTHY`` when health is
    rolled up, so an unobserved component can never be reported as healthy.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILING = "failing"
    UNKNOWN = "unknown"


class HealthCode(StrEnum):
    """Closed condition codes. These are conditions, never instructions."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_STALE = "run_stale"

    MONITOR_INTERNAL_FAILURE = "monitor_internal_failure"
    MONITOR_OBSERVATION_DROPPED = "monitor_observation_dropped"
    CLOCK_REGRESSION = "clock_regression"
    CLOCK_UNREADABLE = "clock_unreadable"

    DATA_FETCH_FAILED = "data_fetch_failed"
    DATA_PROVIDER_HTTP_ERROR = "data_provider_http_error"
    DATA_RATE_LIMITED = "data_rate_limited"
    DATA_UNAVAILABLE = "data_unavailable"

    DATA_DUPLICATES_REMOVED = "data_duplicates_removed"
    DATA_MISSING_ROWS_DROPPED = "data_missing_rows_dropped"
    DATA_INCOMPLETE_ROWS_DROPPED = "data_incomplete_rows_dropped"
    DATA_ROWS_TRIMMED = "data_rows_trimmed"
    DATA_REORDERED = "data_reordered"
    DATA_CONFLICTING_DUPLICATE = "data_conflicting_duplicate"
    DATA_MALFORMED = "data_malformed"

    DATA_STALE = "data_stale"
    DATA_GAP = "data_gap"
    DATA_INTERVAL_ANOMALY = "data_interval_anomaly"
    DATA_FUTURE_TIMESTAMP = "data_future_timestamp"

    SIGNAL_ENGINE_ERROR = "signal_engine_error"
    SIGNAL_DISTRIBUTION_SHIFT = "signal_distribution_shift"
    SIGNAL_PRODUCTION_STALLED = "signal_production_stalled"
    SIGNAL_LATENCY_HIGH = "signal_latency_high"

    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    DELIVERY_NOT_ATTEMPTED = "delivery_not_attempted"
    DELIVERY_TIMEOUT = "delivery_timeout"
    DELIVERY_RATE_LIMITED = "delivery_rate_limited"
    DELIVERY_HTTP_FAILURE = "delivery_http_failure"
    DELIVERY_CONFIG_ERROR = "delivery_config_error"
    DELIVERY_REPEATED_FAILURES = "delivery_repeated_failures"
    CHART_DELIVERY_FAILED = "chart_delivery_failed"

    GOVERNANCE_STATUS_OBSERVED = "governance_status_observed"


# Severity belongs to the condition code, not to the call site, so a caller can
# neither inflate nor suppress it. An ambiguous delivery is a warning: the frozen
# contract says the message may or may not have arrived and is never retried.
SEVERITY_BY_CODE: Mapping[HealthCode, Severity] = MappingProxyType(
    {
        HealthCode.RUN_STARTED: Severity.INFO,
        HealthCode.RUN_COMPLETED: Severity.INFO,
        HealthCode.RUN_FAILED: Severity.CRITICAL,
        HealthCode.RUN_STALE: Severity.WARNING,
        HealthCode.MONITOR_INTERNAL_FAILURE: Severity.CRITICAL,
        HealthCode.MONITOR_OBSERVATION_DROPPED: Severity.WARNING,
        HealthCode.CLOCK_REGRESSION: Severity.WARNING,
        HealthCode.CLOCK_UNREADABLE: Severity.CRITICAL,
        HealthCode.DATA_FETCH_FAILED: Severity.CRITICAL,
        HealthCode.DATA_PROVIDER_HTTP_ERROR: Severity.WARNING,
        HealthCode.DATA_RATE_LIMITED: Severity.WARNING,
        HealthCode.DATA_UNAVAILABLE: Severity.CRITICAL,
        HealthCode.DATA_DUPLICATES_REMOVED: Severity.INFO,
        HealthCode.DATA_MISSING_ROWS_DROPPED: Severity.WARNING,
        HealthCode.DATA_INCOMPLETE_ROWS_DROPPED: Severity.WARNING,
        HealthCode.DATA_ROWS_TRIMMED: Severity.INFO,
        HealthCode.DATA_REORDERED: Severity.WARNING,
        HealthCode.DATA_CONFLICTING_DUPLICATE: Severity.CRITICAL,
        HealthCode.DATA_MALFORMED: Severity.WARNING,
        HealthCode.DATA_STALE: Severity.WARNING,
        HealthCode.DATA_GAP: Severity.WARNING,
        HealthCode.DATA_INTERVAL_ANOMALY: Severity.WARNING,
        HealthCode.DATA_FUTURE_TIMESTAMP: Severity.CRITICAL,
        HealthCode.SIGNAL_ENGINE_ERROR: Severity.CRITICAL,
        HealthCode.SIGNAL_DISTRIBUTION_SHIFT: Severity.INFO,
        HealthCode.SIGNAL_PRODUCTION_STALLED: Severity.WARNING,
        HealthCode.SIGNAL_LATENCY_HIGH: Severity.WARNING,
        HealthCode.DELIVERY_FAILED: Severity.WARNING,
        HealthCode.DELIVERY_UNKNOWN: Severity.WARNING,
        HealthCode.DELIVERY_NOT_ATTEMPTED: Severity.INFO,
        HealthCode.DELIVERY_TIMEOUT: Severity.WARNING,
        HealthCode.DELIVERY_RATE_LIMITED: Severity.WARNING,
        HealthCode.DELIVERY_HTTP_FAILURE: Severity.WARNING,
        HealthCode.DELIVERY_CONFIG_ERROR: Severity.CRITICAL,
        HealthCode.DELIVERY_REPEATED_FAILURES: Severity.CRITICAL,
        HealthCode.CHART_DELIVERY_FAILED: Severity.INFO,
        HealthCode.GOVERNANCE_STATUS_OBSERVED: Severity.INFO,
    }
)


def severity_for(code: HealthCode) -> Severity:
    """Return the fixed severity of a condition code."""
    if not isinstance(code, HealthCode):
        raise MonitoringInputError("code must be a HealthCode")
    return SEVERITY_BY_CODE[code]


def require_utc_timestamp(value: object, name: str) -> datetime:
    """Return a timezone-aware ``datetime``, or raise ``MonitoringInputError``.

    The instant is not rewritten: timezone-aware instants compare correctly
    across offsets and canonical serialization already renders UTC.
    """
    if not isinstance(value, datetime):
        raise MonitoringInputError(f"{name} must be a datetime")
    if value.utcoffset() is None:
        raise MonitoringInputError(f"{name} must be timezone aware")
    return value


def require_subject_id(value: object, name: str = "subject_id") -> str | None:
    """Validate an already-redacted subject identity, or ``None``.

    Only a redaction marker or one of the canonical identity prefixes is
    accepted, so a raw chat id, token, or bare number cannot be stored here.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MonitoringInputError(f"{name} must be a nonempty, trimmed string or None")
    if value.startswith(_REDACTED_MARKER):
        tail = value[len(_REDACTED_MARKER) :]
        if tail == "" or (tail.startswith(":") and len(tail) > 1):
            return value
        raise MonitoringInputError(f"{name} has a malformed redaction marker")
    if not value.startswith(_SUBJECT_ID_PREFIXES):
        raise MonitoringInputError(f"{name} must be redacted or carry a canonical identity prefix")
    return value


def require_detail(value: object) -> tuple[tuple[str, str], ...]:
    """Validate canonical detail pairs: sorted, unique, bounded, secret-free."""
    if not isinstance(value, tuple):
        raise MonitoringInputError("detail must be a tuple of (str, str) pairs")
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise MonitoringInputError("detail must be a tuple of (str, str) pairs")
        key, text = item
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise MonitoringInputError("detail keys must be nonempty trimmed strings")
        if not isinstance(text, str) or not text.strip():
            raise MonitoringInputError("detail values must be nonempty strings")
        if len(text) > _MAX_DETAIL_VALUE_LENGTH:
            raise MonitoringInputError("detail values must be compact, bounded facts")
        lowered = key.lower()
        for fragment in _FORBIDDEN_DETAIL_KEY_PARTS:
            if fragment in lowered:
                raise MonitoringInputError(f"detail key may not reference {fragment}")
    keys = [key for key, _ in value]
    if len(set(keys)) != len(keys):
        raise MonitoringInputError("detail keys must be unique")
    if tuple(sorted(value)) != value:
        raise MonitoringInputError("detail must be sorted by key")
    return value


def _require_event_id(value: object, name: str = "event_id") -> str:
    if not isinstance(value, str) or not value.startswith(_EVENT_ID_PREFIX):
        raise MonitoringInputError(f"{name} must carry the {_EVENT_ID_PREFIX} prefix")
    if len(value) == len(_EVENT_ID_PREFIX):
        raise MonitoringInputError(f"{name} must carry a content identity after the prefix")
    return value


def _require_run_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith(_RUN_ID_PREFIX):
        raise MonitoringInputError(f"run_id must carry the {_RUN_ID_PREFIX} prefix or be None")
    if len(value) == len(_RUN_ID_PREFIX):
        raise MonitoringInputError("run_id must carry a content identity after the prefix")
    return value


def event_identity(
    *,
    component: MonitoredComponent,
    severity: Severity,
    code: HealthCode,
    subject_id: str | None,
    detail: tuple[tuple[str, str], ...],
) -> str:
    """Return the deterministic content identity of a condition.

    Wall-clock time and the run are excluded on purpose: ignoring them is what
    makes a recurring condition deduplicate into one aggregate instead of
    producing an unbounded stream of events.
    """
    if not isinstance(component, MonitoredComponent):
        raise MonitoringInputError("component must be a MonitoredComponent")
    if not isinstance(severity, Severity):
        raise MonitoringInputError("severity must be a Severity")
    if not isinstance(code, HealthCode):
        raise MonitoringInputError("code must be a HealthCode")
    require_subject_id(subject_id)
    require_detail(detail)
    return _EVENT_ID_PREFIX + digest(
        {
            "methodology": _EVENT_METHODOLOGY,
            "component": component,
            "severity": severity,
            "code": code,
            "subject_id": subject_id,
            "detail": detail,
        }
    )


@dataclass(frozen=True, slots=True)
class HealthEvent:
    """One observed condition on one monitored component.

    Attribute-only and immutable. The severity is derived from the code, so an
    inconsistent event cannot be constructed, and the identity is re-derived and
    compared so a forged or stale ``event_id`` is rejected.
    """

    event_id: str
    component: MonitoredComponent
    severity: Severity
    code: HealthCode
    observed_at: datetime
    subject_id: str | None = None
    detail: tuple[tuple[str, str], ...] = ()
    run_id: str | None = None

    def __post_init__(self) -> None:
        _require_event_id(self.event_id)
        if not isinstance(self.component, MonitoredComponent):
            raise MonitoringInputError("component must be a MonitoredComponent")
        if not isinstance(self.code, HealthCode):
            raise MonitoringInputError("code must be a HealthCode")
        if not isinstance(self.severity, Severity):
            raise MonitoringInputError("severity must be a Severity")
        if self.severity is not SEVERITY_BY_CODE[self.code]:
            raise MonitoringInputError("severity is determined by the health code")
        require_utc_timestamp(self.observed_at, "observed_at")
        require_subject_id(self.subject_id)
        require_detail(self.detail)
        _require_run_id(self.run_id)
        expected = event_identity(
            component=self.component,
            severity=self.severity,
            code=self.code,
            subject_id=self.subject_id,
            detail=self.detail,
        )
        if self.event_id != expected:
            raise MonitoringInputError("event_id must be the content identity of the event")

    def to_record(self) -> dict[str, object]:
        """Return a plain mapping; canonicalize it with the shared evidence canon."""
        return {
            "event_id": self.event_id,
            "component": self.component.value,
            "severity": self.severity.value,
            "code": self.code.value,
            "observed_at": self.observed_at,
            "subject_id": self.subject_id,
            "detail": dict(self.detail),
            "run_id": self.run_id,
        }


@dataclass(frozen=True, slots=True)
class HealthEventAggregate:
    """Repeated occurrences of one condition, collapsed into a single record."""

    event_id: str
    component: MonitoredComponent
    severity: Severity
    code: HealthCode
    count: int
    first_seen: datetime
    last_seen: datetime

    def __post_init__(self) -> None:
        _require_event_id(self.event_id)
        if not isinstance(self.component, MonitoredComponent):
            raise MonitoringInputError("component must be a MonitoredComponent")
        if not isinstance(self.code, HealthCode):
            raise MonitoringInputError("code must be a HealthCode")
        if not isinstance(self.severity, Severity):
            raise MonitoringInputError("severity must be a Severity")
        if self.severity is not SEVERITY_BY_CODE[self.code]:
            raise MonitoringInputError("severity is determined by the health code")
        if type(self.count) is not int or self.count < 1:
            raise MonitoringInputError("count must be a positive integer")
        require_utc_timestamp(self.first_seen, "first_seen")
        require_utc_timestamp(self.last_seen, "last_seen")
        if self.first_seen > self.last_seen:
            raise MonitoringInputError("first_seen must not be after last_seen")

    @classmethod
    def from_event(cls, event: HealthEvent) -> HealthEventAggregate:
        """Start an aggregate from the first occurrence of a condition."""
        if not isinstance(event, HealthEvent):
            raise MonitoringInputError("from_event requires a HealthEvent")
        return cls(
            event_id=event.event_id,
            component=event.component,
            severity=event.severity,
            code=event.code,
            count=1,
            first_seen=event.observed_at,
            last_seen=event.observed_at,
        )

    def advance(self, observed_at: datetime) -> HealthEventAggregate:
        """Return a new aggregate with one more occurrence, never mutating this one."""
        return replace(self, count=self.count + 1, last_seen=observed_at)

    def to_record(self) -> dict[str, object]:
        """Return a plain mapping; canonicalize it with the shared evidence canon."""
        return {
            "event_id": self.event_id,
            "component": self.component.value,
            "severity": self.severity.value,
            "code": self.code.value,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


def build_health_event(
    *,
    component: MonitoredComponent,
    code: HealthCode,
    observed_at: datetime,
    subject_id: str | None = None,
    detail: tuple[tuple[str, str], ...] = (),
    run_id: str | None = None,
) -> HealthEvent:
    """Build an event, deriving its severity and content identity.

    Callers never choose a severity: it is a property of the condition code.
    """
    if not isinstance(code, HealthCode):
        raise MonitoringInputError("code must be a HealthCode")
    severity = severity_for(code)
    return HealthEvent(
        event_id=event_identity(
            component=component,
            severity=severity,
            code=code,
            subject_id=subject_id,
            detail=detail,
        ),
        component=component,
        severity=severity,
        code=code,
        observed_at=observed_at,
        subject_id=subject_id,
        detail=detail,
        run_id=run_id,
    )
