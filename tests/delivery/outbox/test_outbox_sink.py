"""Phase 35D unit tests: the durable outbox state machine (locked transitions)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.delivery.outbox.helpers import (
    FakeClock,
    ScriptedInnerSink,
    delivered,
    in_flight,
    project_payload,
    queued_record,
)

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.models import DeliveryState, FailureCategory
from smcsignal.delivery.outbox.models import (
    OutboxConfig,
    OutboxState,
)
from smcsignal.delivery.outbox.sink import OutboxPayloadSink
from smcsignal.delivery.outbox.store import FileOutboxStore

MOMENT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def build(
    tmp_path: Path,
    inner: ScriptedInnerSink,
    *,
    config: OutboxConfig | None = None,
    clock: FakeClock | None = None,
) -> OutboxPayloadSink:
    store = FileOutboxStore(tmp_path / "outbox")
    return OutboxPayloadSink(
        store,
        inner,
        config=config if config is not None else OutboxConfig(cooldown_seconds=0.0),
        clock=clock if clock is not None else FakeClock(),
    )


def test_success_path_queued_in_flight_delivered(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink()
    sink = build(tmp_path, inner)
    payload = project_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None
    assert record.state is OutboxState.DELIVERED
    assert record.attempts_used == 1  # the first attempt counts
    assert record.attempt_log[0].receipt_state is DeliveryState.DELIVERED
    assert record.possibly_duplicated is False
    assert record.ambiguous_attempt_seen is False
    assert inner.calls == 1


def test_sent_receipt_is_terminal_success(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink([(DeliveryState.SENT, FailureCategory.NONE)])
    sink = build(tmp_path, inner)
    payload = project_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.SENT
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED
    assert record.last_receipt_state is DeliveryState.SENT


def test_failed_transport_waits_then_drain_retries_to_success(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink(
        [
            (DeliveryState.FAILED, FailureCategory.TRANSPORT),
            (DeliveryState.DELIVERED, FailureCategory.NONE),
        ]
    )
    clock = FakeClock()
    sink = build(tmp_path, inner, clock=clock)
    payload = project_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.FAILED
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.WAITING
    assert record.attempts_used == 1
    clock.advance(61)
    attempted = sink.drain(clock())
    assert attempted == 1
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED
    assert record.attempts_used == 2
    assert record.possibly_duplicated is False


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.TRANSPORT,
        FailureCategory.NETWORK,
        FailureCategory.RATE_LIMIT,
    ],
)
def test_retryable_failed_categories_retry(tmp_path: Path, buy_snapshot, category) -> None:
    inner = ScriptedInnerSink([(DeliveryState.FAILED, category)])
    sink = build(tmp_path, inner)
    sink.deliver_payload(project_payload(buy_snapshot))
    record = sink.store.all_records()[0]
    assert record.state is OutboxState.WAITING


@pytest.mark.parametrize(
    "state,category",
    [
        (DeliveryState.NOT_ATTEMPTED, FailureCategory.CONFIG),
        (DeliveryState.FAILED, FailureCategory.CONFIG),
        (DeliveryState.FAILED, FailureCategory.BUILD),
        (DeliveryState.FAILED, FailureCategory.CHART),
    ],
)
def test_non_retryable_receipts_are_failed_terminal(
    tmp_path: Path, buy_snapshot, state, category
) -> None:
    inner = ScriptedInnerSink([(state, category)])
    sink = build(tmp_path, inner)
    receipt = sink.deliver_payload(project_payload(buy_snapshot))
    assert receipt.state is state
    record = sink.store.all_records()[0]
    assert record.state is OutboxState.FAILED_TERMINAL
    assert record.attempts_used == 1
    # A second call never touches the inner sink again.
    again = sink.deliver_payload(project_payload(buy_snapshot))
    assert again.state is state
    assert inner.calls == 1


def test_budget_exhaustion_terminal(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink([(DeliveryState.FAILED, FailureCategory.TRANSPORT)])
    sink = build(tmp_path, inner, config=OutboxConfig(max_attempts=2, cooldown_seconds=0.0))
    clock = FakeClock()
    payload = project_payload(buy_snapshot)
    sink.deliver_payload(payload)  # attempt 1 → WAITING
    clock.advance(1)
    sink.drain(clock())  # attempt 2 → budget consumed
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.EXHAUSTED
    assert record.attempts_used == 2
    assert inner.calls == 2
    # Terminal forever: later drains and inline calls never retry.
    clock.advance(1000)
    assert sink.drain(clock()) == 0
    sink.deliver_payload(payload)
    assert inner.calls == 2


def test_unknown_sets_ambiguity_and_later_success_marks_possible_duplicate(
    tmp_path: Path, buy_snapshot
) -> None:
    inner = ScriptedInnerSink(
        [
            (DeliveryState.UNKNOWN, FailureCategory.TIMEOUT),
            (DeliveryState.DELIVERED, FailureCategory.NONE),
        ]
    )
    clock = FakeClock()
    sink = build(tmp_path, inner, clock=clock)
    payload = project_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.UNKNOWN
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.WAITING
    assert record.ambiguous_attempt_seen is True
    assert record.possibly_duplicated is False
    clock.advance(61)
    sink.drain(clock())
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED
    # The duplicate risk of the ambiguous attempt is durably represented.
    assert record.possibly_duplicated is True


def test_unknown_budget_exhaustion_is_ambiguous_terminal(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink([(DeliveryState.UNKNOWN, FailureCategory.TIMEOUT)])
    clock = FakeClock()
    sink = build(
        tmp_path,
        inner,
        config=OutboxConfig(max_attempts=2, cooldown_seconds=0.0),
        clock=clock,
    )
    payload = project_payload(buy_snapshot)
    sink.deliver_payload(payload)  # attempt 1 UNKNOWN → WAITING
    clock.advance(1)
    receipt = sink.deliver_payload(payload)  # attempt 2 UNKNOWN → budget gone
    assert receipt.state is DeliveryState.UNKNOWN
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.AMBIGUOUS
    assert record.possibly_duplicated is True
    assert record.ambiguous_attempt_seen is True
    clock.advance(1000)
    assert sink.drain(clock()) == 0
    assert inner.calls == 2


def test_terminal_success_never_re_attempted(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink()
    sink = build(tmp_path, inner)
    payload = project_payload(buy_snapshot)
    sink.deliver_payload(payload)
    receipt = sink.deliver_payload(payload)  # repeated call
    assert receipt.state is DeliveryState.DELIVERED
    assert inner.calls == 1
    clock = FakeClock()
    clock.advance(10_000)
    assert sink.drain(clock()) == 0
    assert inner.calls == 1


def test_restart_recovery_in_flight_to_waiting(tmp_path: Path, buy_snapshot) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT)
    store.save_record(in_flight(record, attempts=1, at=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = OutboxPayloadSink(store, inner, config=OutboxConfig(cooldown_seconds=0.0), clock=clock)
    recovered = store.load_record(record.delivery_id)
    assert recovered is not None and recovered.state is OutboxState.WAITING
    # The interrupted attempt counted and is ambiguous.
    assert recovered.attempts_used == 1
    assert recovered.ambiguous_attempt_seen is True
    assert recovered.possibly_duplicated is False
    clock.advance(1)
    assert sink.drain(clock()) == 1
    final = store.load_record(record.delivery_id)
    assert final is not None and final.state is OutboxState.DELIVERED
    assert final.possibly_duplicated is True


def test_restart_recovery_in_flight_to_ambiguous_when_budget_spent(
    tmp_path: Path, buy_snapshot
) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT, max_attempts=2)
    store.save_record(in_flight(record, attempts=2, at=MOMENT))
    inner = ScriptedInnerSink()
    # Construction alone performs the locked T9 recovery.
    OutboxPayloadSink(store, inner, config=OutboxConfig(cooldown_seconds=0.0))
    recovered = store.load_record(record.delivery_id)
    assert recovered is not None and recovered.state is OutboxState.AMBIGUOUS
    assert recovered.possibly_duplicated is True
    assert inner.calls == 0


def test_budget_survives_restart(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink([(DeliveryState.FAILED, FailureCategory.TRANSPORT)])
    clock = FakeClock()
    sink = build(
        tmp_path,
        inner,
        config=OutboxConfig(max_attempts=3, cooldown_seconds=0.0),
        clock=clock,
    )
    payload = project_payload(buy_snapshot)
    sink.deliver_payload(payload)  # attempt 1
    # Restart: a brand-new sink over the same store continues the budget.
    sink_two = OutboxPayloadSink(
        sink.store,
        inner,
        config=OutboxConfig(max_attempts=3, cooldown_seconds=0.0),
        clock=clock,
    )
    clock.advance(1)
    assert sink_two.drain(clock()) == 1  # attempt 2
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None
    assert record.attempts_used == 2
    assert record.state is OutboxState.WAITING
    clock.advance(1)
    assert sink_two.drain(clock()) == 1  # attempt 3 → EXHAUSTED
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.EXHAUSTED
    assert record.attempts_used == 3


def test_cooldown_gates_draining(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink([(DeliveryState.FAILED, FailureCategory.TRANSPORT)])
    clock = FakeClock()
    sink = build(
        tmp_path,
        inner,
        config=OutboxConfig(max_attempts=5, cooldown_seconds=60.0),
        clock=clock,
    )
    payload = project_payload(buy_snapshot)
    sink.deliver_payload(payload)
    assert inner.calls == 1
    clock.advance(30)
    assert sink.drain(clock()) == 0  # inside cooldown
    assert inner.calls == 1
    clock.advance(31)
    assert sink.drain(clock()) == 1  # cooldown elapsed
    assert inner.calls == 2


def test_inline_attempt_ignores_cooldown(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink(
        [
            (DeliveryState.FAILED, FailureCategory.TRANSPORT),
            (DeliveryState.DELIVERED, FailureCategory.NONE),
        ]
    )
    clock = FakeClock()
    sink = build(
        tmp_path,
        inner,
        config=OutboxConfig(max_attempts=5, cooldown_seconds=60.0),
        clock=clock,
    )
    payload = project_payload(buy_snapshot)
    sink.deliver_payload(payload)  # WAITING
    receipt = sink.deliver_payload(payload)  # immediate inline re-attempt
    assert receipt.state is DeliveryState.DELIVERED
    assert inner.calls == 2


def test_drain_cap_and_deterministic_order(tmp_path: Path, signal_frames_fixture) -> None:
    from smcsignal.analysis.signal_engine.models import SignalStatus

    buys = [f for f in signal_frames_fixture if f.status is SignalStatus.BUY_SIGNAL][:3]
    inner = ScriptedInnerSink([(DeliveryState.FAILED, FailureCategory.TRANSPORT)])
    store = FileOutboxStore(tmp_path / "outbox")
    clock = FakeClock()
    sink = OutboxPayloadSink(
        store,
        inner,
        config=OutboxConfig(max_attempts=5, cooldown_seconds=0.0, max_per_drain=1),
        clock=clock,
    )
    for frame in buys:
        store.save_record(queued_record(frame, now=MOMENT))
    assert sink.drain(clock()) == 1
    assert sink.drain(clock()) == 1
    assert sink.drain(clock()) == 1
    attempted_ids = [payload.delivery_id for payload in inner.seen]
    assert attempted_ids == sorted(attempted_ids)  # deterministic order


def test_drain_attempts_each_record_at_most_once_per_pass(
    tmp_path: Path, signal_frames_fixture
) -> None:
    from smcsignal.analysis.signal_engine.models import SignalStatus

    buys = [f for f in signal_frames_fixture if f.status is SignalStatus.BUY_SIGNAL][:2]
    inner = ScriptedInnerSink([(DeliveryState.FAILED, FailureCategory.TRANSPORT)])
    store = FileOutboxStore(tmp_path / "outbox")
    sink = OutboxPayloadSink(
        store,
        inner,
        config=OutboxConfig(max_attempts=5, cooldown_seconds=0.0, max_per_drain=8),
        clock=FakeClock(),
    )
    for frame in buys:
        store.save_record(queued_record(frame, now=MOMENT))
    assert sink.drain(MOMENT) == 2
    assert inner.calls == 2  # never twice for one record in one pass


def test_drain_skips_terminal_records(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink()
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT)
    store.save_record(delivered(record, attempts=1, at=MOMENT))
    sink = OutboxPayloadSink(store, inner, config=OutboxConfig(cooldown_seconds=0.0))
    assert sink.drain(MOMENT) == 0
    assert inner.calls == 0


def test_reconciled_queued_record_is_drainable(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink()
    store = FileOutboxStore(tmp_path / "outbox")
    store.save_record(queued_record(buy_snapshot, now=MOMENT))
    sink = OutboxPayloadSink(store, inner, config=OutboxConfig(cooldown_seconds=0.0))
    assert sink.drain(MOMENT) == 1
    record = store.load_record(queued_record(buy_snapshot, now=MOMENT).delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED


def test_chart_payload_survives_retry(tmp_path: Path, buy_snapshot, a_drawing) -> None:
    from tests.delivery.telegram._helpers import chart_payload

    inner = ScriptedInnerSink(
        [
            (DeliveryState.FAILED, FailureCategory.TRANSPORT),
            (DeliveryState.DELIVERED, FailureCategory.NONE),
        ]
    )
    clock = FakeClock()
    sink = build(tmp_path, inner, clock=clock)
    payload = chart_payload(buy_snapshot, a_drawing)
    assert payload.rendered.chart is not None
    sink.deliver_payload(payload)
    clock.advance(1)
    sink.drain(clock())
    retried = inner.seen[-1]
    assert retried.rendered.chart == payload.rendered.chart
    assert retried.rendered.caption == payload.rendered.caption
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED


def test_skipped_duplicate_defensively_treated_as_success(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink([(DeliveryState.SKIPPED_DUPLICATE, FailureCategory.NONE)])
    sink = build(tmp_path, inner)
    payload = project_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.SKIPPED_DUPLICATE
    record = sink.store.load_record(payload.delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED
    # The record is terminal: nothing is ever sent again.
    sink.deliver_payload(payload)
    assert inner.calls == 1


def test_summary_counts_states_and_health(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink()
    sink = build(tmp_path, inner)
    sink.deliver_payload(project_payload(buy_snapshot))
    summary = sink.summary()
    assert summary[OutboxState.DELIVERED.value] == 1
    assert summary[OutboxState.QUEUED.value] == 0
    assert summary["quarantined"] == 0
    assert summary["meta_untrusted"] == 0


def test_in_flight_record_recovered_before_inline_attempt(tmp_path: Path, buy_snapshot) -> None:
    inner = ScriptedInnerSink()
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT)
    store.save_record(in_flight(record, attempts=1, at=MOMENT))
    sink = OutboxPayloadSink(store, inner, config=OutboxConfig(cooldown_seconds=0.0))
    payload = project_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    final = store.load_record(payload.delivery_id)
    assert final is not None
    assert final.attempts_used == 2  # recovered attempt + inline attempt
    assert final.ambiguous_attempt_seen is True
    assert final.possibly_duplicated is True


def test_constructor_validation(tmp_path: Path) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    with pytest.raises(AnalysisInputError):
        OutboxPayloadSink(None, ScriptedInnerSink())  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        OutboxPayloadSink(store, None)  # type: ignore[arg-type]
