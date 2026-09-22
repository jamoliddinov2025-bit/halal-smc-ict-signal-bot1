"""Shared deterministic helpers for the Phase 35D outbox tests.

Everything here is offline: scripted payload sinks, an injectable clock, and
real BUY frames produced by the frozen chain through the existing shared
fixtures. No network access exists on any path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from smcsignal.analysis.signal_engine.models import SignalSnapshot
from smcsignal.delivery.models import DeliveryReceipt, DeliveryState, FailureCategory
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.outbox.models import OutboxRecord, OutboxState, new_queued_record
from smcsignal.delivery.transport import TransportPayload, transport_payload_from_outcome

DESTINATION = "primary"


class FakeClock:
    """Injectable wall clock; advances only when told to."""

    def __init__(self, start: datetime = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)) -> None:
        self.now_value = start

    def __call__(self) -> datetime:
        return self.now_value

    def advance(self, seconds: float) -> None:
        self.now_value = self.now_value + timedelta(seconds=seconds)


def receipt(
    payload: TransportPayload,
    state: DeliveryState,
    category: FailureCategory = FailureCategory.NONE,
) -> DeliveryReceipt:
    return DeliveryReceipt(
        delivery_id=payload.delivery_id,
        message_id=payload.message_id,
        signal_id=payload.signal_id,
        destination_id=payload.destination_id,
        attempt_number=payload.attempt_number,
        state=state,
        failure_category=category,
    )


class ScriptedInnerSink:
    """A scripted inner payload sink standing in for the frozen transport."""

    def __init__(self, outcomes: list[tuple[DeliveryState, FailureCategory]] | None = None):
        self.outcomes: list[tuple[DeliveryState, FailureCategory]] = list(
            outcomes or [(DeliveryState.DELIVERED, FailureCategory.NONE)]
        )
        self.seen: list[TransportPayload] = []

    def deliver_payload(self, payload: TransportPayload) -> DeliveryReceipt:
        self.seen.append(payload)
        state, category = self.outcomes[0] if len(self.outcomes) == 1 else self.outcomes.pop(0)
        return receipt(payload, state, category)

    @property
    def calls(self) -> int:
        return len(self.seen)


def project_payload(frame: SignalSnapshot, destination_id: str = DESTINATION) -> TransportPayload:
    """Project one BUY frame through the frozen render-only coordinator path."""
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=False))
    outcome = coordinator.deliver(frame, destination_id)
    return transport_payload_from_outcome(outcome)


def queued_record(
    frame: SignalSnapshot,
    *,
    now: datetime,
    max_attempts: int = 5,
    destination_id: str = DESTINATION,
    ambiguous: bool = False,
) -> OutboxRecord:
    return new_queued_record(
        project_payload(frame, destination_id),
        now=now,
        max_attempts=max_attempts,
        ambiguous_attempt_seen=ambiguous,
    )


def mutate(record: OutboxRecord, **changes: object) -> OutboxRecord:
    """Deterministically rewrite fields of a frozen record for store seeding."""
    from dataclasses import replace

    return replace(record, **changes)


def _log_entries(count: int, at: datetime) -> tuple:
    """A sequential attempt log of ``count`` settled transport failures."""
    from smcsignal.delivery.outbox.models import AttemptEntry

    return tuple(
        AttemptEntry(
            attempt=index + 1,
            receipt_state=DeliveryState.FAILED,
            failure_category=FailureCategory.TRANSPORT,
            at=at,
        )
        for index in range(count)
    )


def waiting(record: OutboxRecord, *, attempts: int, at: datetime) -> OutboxRecord:
    return mutate(
        record,
        state=OutboxState.WAITING,
        attempts_used=attempts,
        last_attempt_at=at,
        updated_at=at,
        last_receipt_state=DeliveryState.FAILED,
        last_failure_category=FailureCategory.TRANSPORT,
        attempt_log=_log_entries(attempts, at),
    )


def in_flight(record: OutboxRecord, *, attempts: int, at: datetime) -> OutboxRecord:
    # Attempt N is in progress: exactly N-1 settled entries exist.
    return mutate(
        record,
        state=OutboxState.IN_FLIGHT,
        attempts_used=attempts,
        last_attempt_at=at,
        updated_at=at,
        attempt_log=_log_entries(attempts - 1, at),
    )


def delivered(record: OutboxRecord, *, attempts: int, at: datetime) -> OutboxRecord:
    from smcsignal.delivery.outbox.models import AttemptEntry

    failures = _log_entries(attempts - 1, at)
    final = AttemptEntry(
        attempt=attempts,
        receipt_state=DeliveryState.DELIVERED,
        failure_category=FailureCategory.NONE,
        at=at,
    )
    return mutate(
        record,
        state=OutboxState.DELIVERED,
        attempts_used=attempts,
        last_attempt_at=at,
        updated_at=at,
        last_receipt_state=DeliveryState.DELIVERED,
        last_failure_category=FailureCategory.NONE,
        attempt_log=failures + (final,),
    )
