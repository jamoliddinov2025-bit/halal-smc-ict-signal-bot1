"""Phase 25B-1 serialization tests: deterministic, exact, and float-free records."""

from __future__ import annotations

from datetime import UTC, datetime

from smcsignal.analysis.liquidity.evidence import evidence_json
from smcsignal.monitoring import (
    HealthCode,
    HealthEventAggregate,
    MonitoredComponent,
    build_health_event,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)


def _floats_in(value: object) -> list[object]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        found: list[object] = []
        for key, item in value.items():
            found.extend(_floats_in(key))
            found.extend(_floats_in(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_floats_in(item))
        return found
    return []


def test_event_record_exposes_the_documented_fields() -> None:
    event = build_health_event(
        component=MonitoredComponent.DATA_INTEGRITY,
        code=HealthCode.DATA_DUPLICATES_REMOVED,
        observed_at=NOW,
        subject_id="series:BTCUSDT:1h",
        detail=(("removed", "2"),),
        run_id="run:2026-01-01",
    )
    record = event.to_record()
    assert set(record) == {
        "event_id",
        "component",
        "severity",
        "code",
        "observed_at",
        "subject_id",
        "detail",
        "run_id",
    }
    assert record["component"] == "data_integrity"
    assert record["severity"] == "info"
    assert record["code"] == "data_duplicates_removed"
    assert record["detail"] == {"removed": "2"}
    assert record["run_id"] == "run:2026-01-01"


def test_record_serializes_through_the_shared_evidence_canon() -> None:
    event = build_health_event(
        component=MonitoredComponent.MONITORING,
        code=HealthCode.MONITOR_INTERNAL_FAILURE,
        observed_at=NOW,
    )
    rendered = evidence_json(event.to_record())
    assert '"code":"monitor_internal_failure"' in rendered
    assert rendered.startswith("{")
    assert '"observed_at":"2026-01-01T12:00:00.000000Z"' in rendered


def test_records_are_deterministic_and_float_free() -> None:
    event = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=NOW,
    )
    first = evidence_json(event.to_record())
    second = evidence_json(event.to_record())
    assert first == second
    assert _floats_in(event.to_record()) == []


def test_observed_at_is_excluded_from_identity_but_kept_in_the_record() -> None:
    early = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=NOW,
    )
    late = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=LATER,
    )
    assert early.event_id == late.event_id
    assert early.to_record()["observed_at"] != late.to_record()["observed_at"]


def test_aggregate_record_round_trips() -> None:
    event = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_TIMEOUT,
        observed_at=NOW,
    )
    aggregate = HealthEventAggregate.from_event(event).advance(LATER)
    record = aggregate.to_record()
    assert set(record) == {
        "event_id",
        "component",
        "severity",
        "code",
        "count",
        "first_seen",
        "last_seen",
    }
    assert record["count"] == 2
    assert _floats_in(record) == []
    assert evidence_json(record) == evidence_json(aggregate.to_record())


def test_record_is_a_copy_not_a_view() -> None:
    event = build_health_event(
        component=MonitoredComponent.RUN_LIFECYCLE,
        code=HealthCode.RUN_STARTED,
        observed_at=NOW,
    )
    record = event.to_record()
    record["code"] = "tampered"
    assert event.code is HealthCode.RUN_STARTED
    assert event.to_record()["code"] == "run_started"
