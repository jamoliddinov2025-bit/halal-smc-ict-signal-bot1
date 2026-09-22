"""Phase 35D crash-injection tests: explicit crashes at every durable boundary.

Each scenario seeds the durable store to the exact bytes a crash would leave,
constructs a fresh sink (a process restart), and asserts the locked recovery
behavior. The inner transport is always a scripted offline double — the
network is never reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.delivery.outbox.helpers import (
    DESTINATION,
    FakeClock,
    ScriptedInnerSink,
    in_flight,
    project_payload,
    queued_record,
    waiting,
)

from smcsignal.delivery.models import DeliveryState
from smcsignal.delivery.outbox.models import OutboxConfig, OutboxState
from smcsignal.delivery.outbox.reconcile import expected_delivery_id, reconcile_missing
from smcsignal.delivery.outbox.sink import OutboxPayloadSink
from smcsignal.delivery.outbox.store import RECORD_SUFFIX, FileOutboxStore, key_digest

MOMENT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
ZERO_COOLDOWN = OutboxConfig(cooldown_seconds=0.0)


def restart_sink(store: FileOutboxStore, inner: ScriptedInnerSink, clock: FakeClock):
    return OutboxPayloadSink(store, inner, config=ZERO_COOLDOWN, clock=clock)


def crash_1_intent_never_created_window_survives(tmp_path: Path, signal_frames_fixture) -> None:
    """Crash after ledger/window persistence, before any outbox intent.

    The durable window regeneration feeds reconciliation, which recreates the
    missing intent; the drain then delivers it. No loss, no duplicate risk
    (nothing ever reached the transport).
    """
    from smcsignal.analysis.signal_engine.models import SignalStatus

    store = FileOutboxStore(tmp_path / "outbox")
    buys = [f for f in signal_frames_fixture if f.status is SignalStatus.BUY_SIGNAL][:1]
    clock = FakeClock()
    activated = buys[0].candidate.signal.candle_opened_at
    report = reconcile_missing(buys, DESTINATION, activated_at=activated, store=store, clock=clock)
    assert report.created_count == 1
    inner = ScriptedInnerSink()
    sink = restart_sink(store, inner, clock)
    assert sink.drain(clock()) == 1
    delivery_id = expected_delivery_id(buys[0], DESTINATION)
    record = store.load_record(delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED
    assert record.attempts_used == 1
    assert record.possibly_duplicated is False
    assert inner.calls == 1


def crash_2_after_queued_before_any_attempt(tmp_path: Path, buy_snapshot) -> None:
    """Crash right after the QUEUED intent write: zero attempts occurred."""
    store = FileOutboxStore(tmp_path / "outbox")
    store.save_record(queued_record(buy_snapshot, now=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = restart_sink(store, inner, clock)
    assert sink.drain(clock()) == 1
    record = store.all_records()[0]
    assert record.state is OutboxState.DELIVERED
    assert record.attempts_used == 1
    assert record.ambiguous_attempt_seen is False
    assert record.possibly_duplicated is False


def crash_3_after_in_flight_before_transport_call(tmp_path: Path, buy_snapshot) -> None:
    """Crash after IN_FLIGHT but before the transport call.

    Restart must treat the started attempt as ambiguous (it *might* have
    reached the transport), retry within budget, and flag the duplicate risk.
    """
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT)
    store.save_record(in_flight(record, attempts=1, at=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = restart_sink(store, inner, clock)
    recovered = store.all_records()[0]
    assert recovered.state is OutboxState.WAITING
    assert recovered.ambiguous_attempt_seen is True
    assert sink.drain(clock()) == 1
    final = store.all_records()[0]
    assert final.state is OutboxState.DELIVERED
    assert final.possibly_duplicated is True
    assert inner.calls == 1  # exactly one new send


def crash_4_during_transport_call_unknown(tmp_path: Path, buy_snapshot) -> None:
    """Crash mid-call is indistinguishable from crash 3; identical recovery.

    The eventual success after an ambiguous attempt carries the durable
    duplicate-risk marker.
    """
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT)
    store.save_record(in_flight(record, attempts=1, at=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = restart_sink(store, inner, clock)
    sink.drain(clock())
    final = store.all_records()[0]
    assert final.state is OutboxState.DELIVERED
    assert final.ambiguous_attempt_seen is True
    assert final.possibly_duplicated is True
    assert final.attempts_used == 2
    # The interrupted attempt is logged as the ambiguous outcome it was.
    assert [entry.receipt_state for entry in final.attempt_log] == [
        DeliveryState.UNKNOWN,
        DeliveryState.DELIVERED,
    ]


def crash_5_after_transport_success_before_receipt_write(tmp_path: Path, buy_snapshot) -> None:
    """The one unavoidable duplicate window: the transport accepted, the
    receipt was never persisted. Restart recovers as ambiguous, retries, and
    the remote message may be duplicated — durably flagged."""
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT)
    store.save_record(in_flight(record, attempts=1, at=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = restart_sink(store, inner, clock)
    sink.drain(clock())
    final = store.all_records()[0]
    assert final.state is OutboxState.DELIVERED
    assert final.possibly_duplicated is True
    assert inner.calls == 1


def crash_6_after_receipt_persistence(tmp_path: Path, buy_snapshot) -> None:
    """A persisted terminal receipt survives untouched; nothing re-sends."""
    from tests.delivery.outbox.helpers import delivered

    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT)
    store.save_record(delivered(record, attempts=1, at=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = restart_sink(store, inner, clock)
    assert sink.drain(clock()) == 0
    receipt = sink.deliver_payload(project_payload(buy_snapshot))
    assert receipt.state is DeliveryState.DELIVERED
    assert inner.calls == 0
    final = store.all_records()[0]
    assert final.state is OutboxState.DELIVERED
    assert final.attempts_used == 1


def crash_7_during_reconciliation(tmp_path: Path, signal_frames_fixture) -> None:
    """An interrupted reconciliation completes deterministically on restart."""
    from smcsignal.analysis.signal_engine.models import SignalStatus

    store = FileOutboxStore(tmp_path / "outbox")
    buys = [f for f in signal_frames_fixture if f.status is SignalStatus.BUY_SIGNAL]
    activated = buys[0].candidate.signal.candle_opened_at
    clock = FakeClock()
    partial = reconcile_missing(
        buys[:2], DESTINATION, activated_at=activated, store=store, clock=clock
    )
    assert partial.created_count == 2  # crash here
    full = reconcile_missing(buys, DESTINATION, activated_at=activated, store=store, clock=clock)
    assert full.created_count == len(buys) - 2
    assert full.skipped_existing == 2
    records = store.all_records()
    assert len(records) == len(buys)
    assert len({record.delivery_id for record in records}) == len(buys)


def crash_8_during_drain_retry(tmp_path: Path, buy_snapshot) -> None:
    """Crash during a drain attempt: IN_FLIGHT with attempts=2, budget kept."""
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT, max_attempts=5)
    store.save_record(waiting(record, attempts=1, at=MOMENT))
    store.save_record(in_flight(waiting(record, attempts=1, at=MOMENT), attempts=2, at=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = restart_sink(store, inner, clock)
    recovered = store.all_records()[0]
    assert recovered.state is OutboxState.WAITING
    assert recovered.attempts_used == 2
    assert recovered.ambiguous_attempt_seen is True
    assert sink.drain(clock()) == 1
    final = store.all_records()[0]
    assert final.state is OutboxState.DELIVERED
    assert final.attempts_used == 3
    assert final.possibly_duplicated is True


def crash_9_corrupted_record_recreated_with_ambiguity(tmp_path: Path, buy_snapshot) -> None:
    """A corrupted record is quarantined; reconciliation recreates the intent
    with the ambiguity flag (prior history unknowable)."""

    store = FileOutboxStore(tmp_path / "outbox")
    delivery_id = expected_delivery_id(buy_snapshot, DESTINATION)
    path = tmp_path / "outbox" / "records" / (key_digest(delivery_id) + RECORD_SUFFIX)
    path.write_text("{broken", encoding="utf-8")
    assert store.load_record(delivery_id) is None
    assert store.quarantine_count == 1
    report = reconcile_missing(
        [buy_snapshot],
        DESTINATION,
        activated_at=buy_snapshot.candidate.signal.candle_opened_at,
        store=store,
        clock=lambda: MOMENT,
    )
    assert report.created == (delivery_id,)
    record = store.load_record(delivery_id)
    assert record is not None
    assert record.ambiguous_attempt_seen is True
    assert record.state is OutboxState.QUEUED


def crash_10_stale_partial_and_incomplete_records(tmp_path: Path, buy_snapshot) -> None:
    """Leftover partials are swept; an unknown-version record is quarantined."""
    store_dir = tmp_path / "outbox"
    (store_dir / "records").mkdir(parents=True)
    stale = store_dir / "records" / ("a" * 64 + RECORD_SUFFIX + ".partial")
    stale.write_text("incomplete", encoding="utf-8")
    store = FileOutboxStore(store_dir)
    assert not stale.exists()
    delivery_id = expected_delivery_id(buy_snapshot, DESTINATION)
    path = store_dir / "records" / (key_digest(delivery_id) + RECORD_SUFFIX)
    record = queued_record(buy_snapshot, now=MOMENT)
    document = record.to_document()
    document["schema"] = "smcsignal.outbox.record/v2"
    import json

    path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load_record(delivery_id) is None
    assert store.quarantine_count == 1
    # Recovery path stays safe: nothing to drain, nothing lost silently.
    inner = ScriptedInnerSink()
    sink = restart_sink(store, inner, FakeClock())
    assert sink.drain(MOMENT) == 0


def crash_budget_consumed_in_flight_is_terminal_ambiguous(tmp_path: Path, buy_snapshot) -> None:
    """IN_FLIGHT at the budget edge restarts as terminal AMBIGUOUS."""
    store = FileOutboxStore(tmp_path / "outbox")
    record = queued_record(buy_snapshot, now=MOMENT, max_attempts=2)
    store.save_record(in_flight(record, attempts=2, at=MOMENT))
    inner = ScriptedInnerSink()
    clock = FakeClock()
    sink = restart_sink(store, inner, clock)
    final = store.all_records()[0]
    assert final.state is OutboxState.AMBIGUOUS
    assert final.possibly_duplicated is True
    assert sink.drain(clock()) == 0
    assert inner.calls == 0
