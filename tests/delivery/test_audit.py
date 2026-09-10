"""Phase 24C audit tests: deterministic, machine-readable, secret-free records."""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery import (
    DeliveryAuditRecord,
    DeliveryCoordinator,
    FakeTransportSink,
    audit_record_for,
    redact,
)


def test_redact_is_deterministic_and_lossy() -> None:
    assert redact("channel-buys") == redact("channel-buys")
    assert "<redacted>" in redact("channel-buys")
    # full value never appears verbatim
    assert "channel-buys" not in redact("channel-buys")
    assert redact("abc", keep=4) == "<redacted>"  # short value fully hidden


def test_redact_keeps_only_suffix() -> None:
    value = "some-long-destination-key"
    out = redact(value)
    assert out.startswith("<redacted>")
    assert out.endswith(value[-4:])


def test_audit_record_is_deterministic_for_identical_input(buy_snapshot) -> None:
    def make():
        outcome = DeliveryCoordinator(FakeTransportSink()).deliver(buy_snapshot, "channel-buys")
        return audit_record_for(outcome)

    first = make()
    second = make()
    assert first == second
    assert isinstance(first, DeliveryAuditRecord)
    assert first.delivery_id == second.delivery_id
    assert first.message_digest == second.message_digest


def test_audit_record_redacts_destination_and_has_no_secrets(buy_snapshot) -> None:
    outcome = DeliveryCoordinator(FakeTransportSink()).deliver(buy_snapshot, "channel-buys")
    record = audit_record_for(outcome)
    assert record.destination_redacted == redact("channel-buys")
    assert "channel-buys" not in record.destination_redacted
    # the record exposes only digest/redacted/identity fields
    from dataclasses import fields

    for field in fields(DeliveryAuditRecord):
        raw = getattr(record, field.name)
        if isinstance(raw, str):
            assert "channel-buys" not in raw
    assert len(record.message_digest) == 64  # sha256 hex digest
    assert record.signal_id == buy_snapshot.signal_id
    assert record.attempt_number == 1
    assert record.state.value == "DELIVERED"


def test_audit_record_for_rejects_non_outcome() -> None:
    with pytest.raises(AnalysisInputError):
        audit_record_for(object())  # type: ignore[arg-type]


def test_audit_record_reflects_chart_artifact_id(buy_snapshot, a_drawing) -> None:
    outcome = DeliveryCoordinator(FakeTransportSink()).deliver(
        buy_snapshot, "channel-buys", drawing=a_drawing
    )
    record = audit_record_for(outcome)
    assert record.chart_artifact_id == outcome.chart_artifact_id
    assert record.chart_artifact_id is not None
    assert record.chart_artifact_id.startswith("chart:")


def test_audit_record_is_machine_readable_dictlike(buy_snapshot) -> None:
    outcome = DeliveryCoordinator(FakeTransportSink()).deliver(buy_snapshot, "channel-buys")
    record = audit_record_for(outcome)
    as_dict = {
        "delivery_id": record.delivery_id,
        "message_id": record.message_id,
        "signal_id": record.signal_id,
        "destination_redacted": record.destination_redacted,
        "state": record.state.value,
        "failure_category": record.failure_category.value,
        "attempt_number": record.attempt_number,
        "message_digest": record.message_digest,
    }
    # all values are plain JSON-serializable primitives (no datetimes/objects)
    assert all(isinstance(v, (str, int)) for v in as_dict.values())
