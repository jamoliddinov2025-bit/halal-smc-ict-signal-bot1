"""Phase 35D unit tests: outbox states, records, configuration, identities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.delivery.outbox.helpers import FakeClock, project_payload

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.models import DeliveryState, FailureCategory
from smcsignal.delivery.outbox.models import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_PER_DRAIN,
    MAX_MAX_ATTEMPTS,
    META_SCHEMA,
    MIN_MAX_ATTEMPTS,
    RECORD_SCHEMA,
    TERMINAL_STATES,
    OutboxConfig,
    OutboxRecord,
    OutboxState,
    ensure_utc,
    meta_document,
    new_queued_record,
    parse_meta_document,
    receipt_allows_retry,
)

MOMENT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def test_seven_locked_states_exist_exactly() -> None:
    assert {state.value for state in OutboxState} == {
        "QUEUED",
        "IN_FLIGHT",
        "WAITING",
        "DELIVERED",
        "FAILED_TERMINAL",
        "EXHAUSTED",
        "AMBIGUOUS",
    }
    assert TERMINAL_STATES == frozenset(
        {
            OutboxState.DELIVERED,
            OutboxState.FAILED_TERMINAL,
            OutboxState.EXHAUSTED,
            OutboxState.AMBIGUOUS,
        }
    )


def test_locked_config_defaults() -> None:
    config = OutboxConfig()
    assert config.max_attempts == DEFAULT_MAX_ATTEMPTS == 5
    assert config.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS == 60.0
    assert config.max_per_drain == DEFAULT_MAX_PER_DRAIN == 8


@pytest.mark.parametrize("attempts", [MIN_MAX_ATTEMPTS, MAX_MAX_ATTEMPTS])
def test_config_attempt_range_boundaries_accepted(attempts: int) -> None:
    assert OutboxConfig(max_attempts=attempts).max_attempts == attempts


@pytest.mark.parametrize("attempts", [0, 11, -3, True])
def test_config_attempt_range_rejected(attempts: object) -> None:
    with pytest.raises(AnalysisInputError):
        OutboxConfig(max_attempts=attempts)  # type: ignore[arg-type]


@pytest.mark.parametrize("cooldown", [-0.1, float("nan"), float("inf"), "5"])
def test_config_cooldown_rejected(cooldown: object) -> None:
    with pytest.raises(AnalysisInputError):
        OutboxConfig(cooldown_seconds=cooldown)  # type: ignore[arg-type]


@pytest.mark.parametrize("cap", [0, -1, True])
def test_config_drain_cap_rejected(cap: object) -> None:
    with pytest.raises(AnalysisInputError):
        OutboxConfig(max_per_drain=cap)  # type: ignore[arg-type]


def test_retryability_matrix() -> None:
    # UNKNOWN is retryable regardless of category (ambiguous, not failed).
    for category in FailureCategory:
        assert receipt_allows_retry(DeliveryState.UNKNOWN, category) is True
    # FAILED is retryable exactly for the transport/network/rate-limit family.
    assert receipt_allows_retry(DeliveryState.FAILED, FailureCategory.TRANSPORT)
    assert receipt_allows_retry(DeliveryState.FAILED, FailureCategory.NETWORK)
    assert receipt_allows_retry(DeliveryState.FAILED, FailureCategory.RATE_LIMIT)
    for category in (
        FailureCategory.CONFIG,
        FailureCategory.BUILD,
        FailureCategory.CHART,
        FailureCategory.TIMEOUT,
        FailureCategory.UNKNOWN,
        FailureCategory.NONE,
    ):
        assert not receipt_allows_retry(DeliveryState.FAILED, category)
    # Successes, no-ops, and dedup skips are never retried.
    for state in (
        DeliveryState.DELIVERED,
        DeliveryState.SENT,
        DeliveryState.NOT_ATTEMPTED,
        DeliveryState.SKIPPED_DUPLICATE,
    ):
        assert not receipt_allows_retry(state, FailureCategory.TRANSPORT)


def test_ensure_utc_normalizes() -> None:
    naive = datetime(2026, 9, 22, 12, 0)
    assert ensure_utc(naive) == datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    plus_two = ensure_utc(datetime(2026, 9, 22, 14, 0, tzinfo=UTC) + timedelta(hours=0))
    assert plus_two.utcoffset() == timedelta(0)


def test_new_queued_record_fields(buy_snapshot) -> None:
    payload = project_payload(buy_snapshot)
    record = new_queued_record(payload, now=MOMENT, max_attempts=5)
    assert record.state is OutboxState.QUEUED
    assert record.attempts_used == 0
    assert record.max_attempts == 5
    assert record.delivery_id == payload.delivery_id
    assert record.message_id == payload.message_id
    assert record.signal_id == payload.signal_id
    assert record.destination_id == payload.destination_id
    assert record.created_at == MOMENT
    assert record.updated_at == MOMENT
    assert record.last_attempt_at is None
    assert record.last_receipt_state is None
    assert record.last_failure_category is None
    assert record.ambiguous_attempt_seen is False
    assert record.possibly_duplicated is False
    assert record.attempt_log == ()
    assert record.payload.caption == payload.rendered.caption
    assert record.payload.parts == payload.rendered.parts
    assert record.budget_remaining is True
    assert record.terminal is False


def test_queued_record_ambiguity_flag_for_quarantine_recreation(buy_snapshot) -> None:
    payload = project_payload(buy_snapshot)
    record = new_queued_record(payload, now=MOMENT, max_attempts=5, ambiguous_attempt_seen=True)
    assert record.ambiguous_attempt_seen is True
    assert record.possibly_duplicated is False


def test_record_document_round_trip(buy_snapshot) -> None:
    payload = project_payload(buy_snapshot)
    record = new_queued_record(payload, now=MOMENT, max_attempts=5)
    document = record.to_document()
    assert document["schema"] == RECORD_SCHEMA
    restored = OutboxRecord.from_document(document)
    assert restored == record


def test_record_document_round_trip_with_chart(buy_snapshot, a_drawing) -> None:
    from tests.delivery.telegram._helpers import chart_payload

    payload = chart_payload(buy_snapshot, a_drawing)
    record = new_queued_record(payload, now=MOMENT, max_attempts=5)
    assert record.payload.chart is not None
    restored = OutboxRecord.from_document(record.to_document())
    assert restored == record
    assert restored.payload.chart == record.payload.chart


@pytest.mark.parametrize(
    "mutate_key",
    sorted(
        {
            "schema",
            "delivery_id",
            "message_id",
            "signal_id",
            "destination_id",
            "state",
            "attempts_used",
            "max_attempts",
            "created_at",
            "updated_at",
            "last_attempt_at",
            "last_receipt_state",
            "last_failure_category",
            "ambiguous_attempt_seen",
            "possibly_duplicated",
            "attempt_log",
            "payload",
        }
    ),
)
def test_record_missing_field_rejected(buy_snapshot, mutate_key: str) -> None:
    record = new_queued_record(project_payload(buy_snapshot), now=MOMENT, max_attempts=5)
    document = record.to_document()
    del document[mutate_key]
    with pytest.raises(AnalysisInputError):
        OutboxRecord.from_document(document)


def test_record_unknown_field_rejected(buy_snapshot) -> None:
    document = new_queued_record(
        project_payload(buy_snapshot), now=MOMENT, max_attempts=5
    ).to_document()
    document["surprise"] = 1
    with pytest.raises(AnalysisInputError):
        OutboxRecord.from_document(document)


def test_record_wrong_schema_rejected(buy_snapshot) -> None:
    document = new_queued_record(
        project_payload(buy_snapshot), now=MOMENT, max_attempts=5
    ).to_document()
    document["schema"] = "smcsignal.outbox.record/v2"
    with pytest.raises(AnalysisInputError):
        OutboxRecord.from_document(document)


def test_record_invariants_enforced(buy_snapshot) -> None:
    record = new_queued_record(project_payload(buy_snapshot), now=MOMENT, max_attempts=5)
    from dataclasses import replace

    with pytest.raises(AnalysisInputError):
        replace(record, attempts_used=6)
    with pytest.raises(AnalysisInputError):
        replace(record, delivery_id="not-a-delivery")
    with pytest.raises(AnalysisInputError):
        replace(record, message_id="delivery:x")
    with pytest.raises(AnalysisInputError):
        replace(record, max_attempts=0)


def test_meta_document_round_trip() -> None:
    document = meta_document(MOMENT)
    assert document["schema"] == META_SCHEMA
    assert parse_meta_document(document) == MOMENT


def test_meta_document_rejects_unknown_schema() -> None:
    document = meta_document(MOMENT)
    document["schema"] = "smcsignal.outbox.meta/v9"
    with pytest.raises(AnalysisInputError):
        parse_meta_document(document)


def test_meta_document_rejects_extra_field() -> None:
    document = meta_document(MOMENT)
    document["extra"] = "x"
    with pytest.raises(AnalysisInputError):
        parse_meta_document(document)


def test_clock_injection_determinism(buy_snapshot) -> None:
    clock = FakeClock()
    one = new_queued_record(project_payload(buy_snapshot), now=clock(), max_attempts=5)
    two = new_queued_record(project_payload(buy_snapshot), now=clock(), max_attempts=5)
    assert one == two
