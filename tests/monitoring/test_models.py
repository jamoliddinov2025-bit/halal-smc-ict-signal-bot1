"""Phase 25B-1 model tests: closed enums, fixed severity, content identity, dedup."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from smcsignal.monitoring import (
    SEVERITY_BY_CODE,
    HealthCode,
    HealthEvent,
    HealthEventAggregate,
    HealthState,
    MonitoredComponent,
    MonitoringInputError,
    Severity,
    build_health_event,
    event_identity,
    require_detail,
    require_subject_id,
    require_utc_timestamp,
    severity_for,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)


def test_health_states_are_exactly_four() -> None:
    # The approved design fixes four states. Inventing a fifth (or dropping
    # UNKNOWN, which prevents a false all-clear) must fail loudly.
    assert {state.value for state in HealthState} == {
        "healthy",
        "degraded",
        "failing",
        "unknown",
    }


def test_components_are_a_closed_set() -> None:
    assert {component.value for component in MonitoredComponent} == {
        "market_data",
        "data_integrity",
        "signal_engine",
        "delivery",
        "telegram_transport",
        "monitoring",
        "governance_observer",
        "run_lifecycle",
    }


@pytest.mark.parametrize("code", list(HealthCode))
def test_every_code_has_a_fixed_severity(code: HealthCode) -> None:
    assert severity_for(code) is SEVERITY_BY_CODE[code]
    assert isinstance(severity_for(code), Severity)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (HealthCode.RUN_FAILED, Severity.CRITICAL),
        (HealthCode.DELIVERY_UNKNOWN, Severity.WARNING),
        (HealthCode.DELIVERY_CONFIG_ERROR, Severity.CRITICAL),
        (HealthCode.CHART_DELIVERY_FAILED, Severity.INFO),
        (HealthCode.DELIVERY_NOT_ATTEMPTED, Severity.INFO),
    ],
)
def test_severity_is_a_property_of_the_code(code: HealthCode, expected: Severity) -> None:
    assert severity_for(code) is expected


def test_severity_for_rejects_a_non_code() -> None:
    with pytest.raises(MonitoringInputError):
        severity_for("run_failed")  # type: ignore[arg-type]


def test_build_event_derives_severity_and_identity() -> None:
    event = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_UNKNOWN,
        observed_at=NOW,
        subject_id="<redacted>:1234",
    )
    assert event.severity is Severity.WARNING
    assert event.event_id.startswith("health:")
    assert event.subject_id == "<redacted>:1234"


def test_event_rejects_a_caller_chosen_severity() -> None:
    identity = event_identity(
        component=MonitoredComponent.DELIVERY,
        severity=Severity.INFO,
        code=HealthCode.DELIVERY_UNKNOWN,
        subject_id=None,
        detail=(),
    )
    with pytest.raises(MonitoringInputError):
        HealthEvent(
            event_id=identity,
            component=MonitoredComponent.DELIVERY,
            severity=Severity.INFO,
            code=HealthCode.DELIVERY_UNKNOWN,
            observed_at=NOW,
        )


def test_event_rejects_a_forged_identity() -> None:
    with pytest.raises(MonitoringInputError):
        HealthEvent(
            event_id="health:" + "0" * 64,
            component=MonitoredComponent.MONITORING,
            severity=Severity.CRITICAL,
            code=HealthCode.MONITOR_INTERNAL_FAILURE,
            observed_at=NOW,
        )


def test_event_identity_is_deterministic() -> None:
    first = event_identity(
        component=MonitoredComponent.DATA_INTEGRITY,
        severity=Severity.INFO,
        code=HealthCode.DATA_DUPLICATES_REMOVED,
        subject_id="series:BTCUSDT:1h",
        detail=(("count", "2"),),
    )
    second = event_identity(
        component=MonitoredComponent.DATA_INTEGRITY,
        severity=Severity.INFO,
        code=HealthCode.DATA_DUPLICATES_REMOVED,
        subject_id="series:BTCUSDT:1h",
        detail=(("count", "2"),),
    )
    assert first == second


def test_event_identity_excludes_time_and_run() -> None:
    # Deduplication depends on this: the same recurring condition must collapse
    # into one aggregate no matter when it is observed or which run saw it.
    early = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=NOW,
        run_id="run:first",
    )
    late = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=LATER,
        run_id="run:second",
    )
    assert early.event_id == late.event_id


@pytest.mark.parametrize(
    "changed",
    [
        {"subject_id": "signal:other"},
        {"detail": (("attempt", "2"),)},
        {"code": HealthCode.DELIVERY_FAILED},
    ],
)
def test_event_identity_separates_distinct_conditions(changed: dict[str, object]) -> None:
    base: dict[str, object] = {
        "component": MonitoredComponent.DELIVERY,
        "code": HealthCode.DELIVERY_TIMEOUT,
        "observed_at": NOW,
        "subject_id": "signal:one",
    }
    base.update(changed)
    reference = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=NOW,
        subject_id="signal:one",
    )
    other = build_health_event(**base)  # type: ignore[arg-type]
    assert reference.event_id != other.event_id


def test_event_is_immutable() -> None:
    event = build_health_event(
        component=MonitoredComponent.MONITORING,
        code=HealthCode.MONITOR_OBSERVATION_DROPPED,
        observed_at=NOW,
    )
    with pytest.raises(FrozenInstanceError):
        event.event_id = "health:" + "1" * 64  # type: ignore[misc]


def test_aggregate_collapses_repetition_without_mutating() -> None:
    event = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=NOW,
    )
    first = HealthEventAggregate.from_event(event)
    assert (first.count, first.first_seen, first.last_seen) == (1, NOW, NOW)
    second = first.advance(LATER)
    assert (second.count, second.last_seen) == (2, LATER)
    # The original aggregate is untouched.
    assert (first.count, first.last_seen) == (1, NOW)


def test_aggregate_rejects_a_backwards_clock() -> None:
    event = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=LATER,
    )
    aggregate = HealthEventAggregate.from_event(event)
    with pytest.raises(MonitoringInputError):
        aggregate.advance(NOW)


def test_aggregate_requires_a_positive_count() -> None:
    with pytest.raises(MonitoringInputError):
        HealthEventAggregate(
            event_id="health:" + "0" * 64,
            component=MonitoredComponent.DELIVERY,
            severity=Severity.WARNING,
            code=HealthCode.DELIVERY_TIMEOUT,
            count=0,
            first_seen=NOW,
            last_seen=NOW,
        )


def test_aggregate_from_event_rejects_a_non_event() -> None:
    with pytest.raises(MonitoringInputError):
        HealthEventAggregate.from_event("not-an-event")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1, 12, 0),  # naive
        "2026-01-01T12:00:00Z",  # not a datetime
        1_767_268_800,  # not a datetime
    ],
)
def test_utc_timestamp_requires_an_aware_datetime(value: object) -> None:
    with pytest.raises(MonitoringInputError):
        require_utc_timestamp(value, "observed_at")


def test_utc_timestamp_accepts_an_aware_instant() -> None:
    assert require_utc_timestamp(NOW, "observed_at") is NOW


@pytest.mark.parametrize(
    "value",
    [None, "<redacted>", "<redacted>:1234", "signal:abc", "series:BTCUSDT:1h", "run:xyz"],
)
def test_subject_id_accepts_redacted_or_canonical_values(value: str | None) -> None:
    assert require_subject_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "123456789",  # a bare chat id
        "-1001234567890",  # a bare channel id
        "123456789:AAsomeTokenValue",  # a token-shaped value
        "<redacted>:",  # malformed marker
        "<redacted>x",  # malformed marker
        " ticker:BTCUSDT",  # untrimmed
        "",  # empty
        42,  # not a string
    ],
)
def test_subject_id_rejects_anything_that_is_not_safe(value: object) -> None:
    with pytest.raises(MonitoringInputError):
        require_subject_id(value)


@pytest.mark.parametrize(
    "key",
    ["api_key", "bot_token", "chat_id", "caption", "authorization", "endpoint_url", "secret"],
)
def test_detail_refuses_keys_that_could_leak_secrets_or_content(key: str) -> None:
    with pytest.raises(MonitoringInputError):
        require_detail(((key, "value"),))


def test_detail_requires_canonical_sorted_unique_pairs() -> None:
    assert require_detail((("a", "1"), ("b", "2"))) == (("a", "1"), ("b", "2"))
    with pytest.raises(MonitoringInputError):
        require_detail((("b", "2"), ("a", "1")))  # unsorted
    with pytest.raises(MonitoringInputError):
        require_detail((("a", "1"), ("a", "2")))  # duplicate keys
    with pytest.raises(MonitoringInputError):
        require_detail([["a", "1"]])  # not a tuple of tuples
    with pytest.raises(MonitoringInputError):
        require_detail((("a", "x" * 500),))  # not a compact fact
