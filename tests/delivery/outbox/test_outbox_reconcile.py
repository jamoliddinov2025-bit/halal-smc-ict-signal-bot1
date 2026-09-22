"""Phase 35D unit tests: deterministic startup reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.delivery.outbox.helpers import DESTINATION, delivered, project_payload, queued_record

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.signal_engine.models import SignalStatus
from smcsignal.delivery.identity import delivery_identity, message_identity
from smcsignal.delivery.outbox.models import OutboxConfig, OutboxState
from smcsignal.delivery.outbox.reconcile import (
    expected_delivery_id,
    reconcile_missing,
)
from smcsignal.delivery.outbox.store import (
    CORRUPT_SUFFIX,
    RECORD_SUFFIX,
    FileOutboxStore,
    key_digest,
)

MOMENT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def buy_frames(frames) -> list:
    return [frame for frame in frames if frame.status is SignalStatus.BUY_SIGNAL]


def test_expected_identity_matches_the_frozen_chain(signal_frames_fixture) -> None:
    frame = buy_frames(signal_frames_fixture)[0]
    assert expected_delivery_id(frame, DESTINATION) == delivery_identity(
        message_identity(frame.signal_id), DESTINATION
    )


def test_expected_identity_rejects_non_buy(signal_frames_fixture) -> None:
    non_buy = next(
        frame for frame in signal_frames_fixture if frame.status is not SignalStatus.BUY_SIGNAL
    )
    with pytest.raises(AnalysisInputError):
        expected_delivery_id(non_buy, DESTINATION)


def test_reconciliation_creates_missing_intents(tmp_path: Path, signal_frames_fixture) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    buys = buy_frames(signal_frames_fixture)
    activation = buys[0].candidate.signal.candle_opened_at
    report = reconcile_missing(
        signal_frames_fixture,
        DESTINATION,
        activated_at=activation,
        store=store,
        clock=lambda: MOMENT,
    )
    assert report.considered == len(buys)
    assert report.skipped_pre_activation == 0
    assert report.skipped_existing == 0
    assert report.created == tuple(expected_delivery_id(f, DESTINATION) for f in buys)
    for frame in buys:
        record = store.load_record(expected_delivery_id(frame, DESTINATION))
        assert record is not None
        assert record.state is OutboxState.QUEUED
        assert record.attempts_used == 0
        assert record.ambiguous_attempt_seen is False
        assert record.signal_id == frame.signal_id


def test_reconciled_payload_is_byte_identical_to_live_projection(
    tmp_path: Path, signal_frames_fixture
) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    frame = buy_frames(signal_frames_fixture)[0]
    reconcile_missing(
        [frame],
        DESTINATION,
        activated_at=frame.candidate.signal.candle_opened_at,
        store=store,
        clock=lambda: MOMENT,
    )
    record = store.load_record(expected_delivery_id(frame, DESTINATION))
    live = project_payload(frame, DESTINATION)
    assert record is not None
    assert record.payload.caption == live.rendered.caption
    assert record.payload.parts == live.rendered.parts
    assert record.message_id == live.message_id
    assert record.delivery_id == live.delivery_id


def test_activation_marker_excludes_history(tmp_path: Path, signal_frames_fixture) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    buys = buy_frames(signal_frames_fixture)
    # Activation after the first two BUY candles: only later candles qualify.
    activation = buys[1].candidate.signal.candle_opened_at + timedelta(seconds=1)
    report = reconcile_missing(
        signal_frames_fixture,
        DESTINATION,
        activated_at=activation,
        store=store,
        clock=lambda: MOMENT,
    )
    assert report.skipped_pre_activation == 2
    assert report.created == tuple(expected_delivery_id(f, DESTINATION) for f in buys[2:])


def test_terminal_records_are_never_re_enqueued(tmp_path: Path, signal_frames_fixture) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    frame = buy_frames(signal_frames_fixture)[0]
    existing = queued_record(frame, now=MOMENT)
    store.save_record(delivered(existing, attempts=1, at=MOMENT))
    report = reconcile_missing(
        [frame],
        DESTINATION,
        activated_at=frame.candidate.signal.candle_opened_at,
        store=store,
        clock=lambda: MOMENT,
    )
    assert report.created == ()
    assert report.skipped_existing == 1
    record = store.load_record(existing.delivery_id)
    assert record is not None and record.state is OutboxState.DELIVERED
    assert record.attempts_used == 1


def test_non_terminal_records_are_left_to_the_drain(tmp_path: Path, signal_frames_fixture) -> None:
    from tests.delivery.outbox.helpers import waiting

    store = FileOutboxStore(tmp_path / "outbox")
    frame = buy_frames(signal_frames_fixture)[0]
    existing = waiting(queued_record(frame, now=MOMENT), attempts=1, at=MOMENT)
    store.save_record(existing)
    report = reconcile_missing(
        [frame],
        DESTINATION,
        activated_at=frame.candidate.signal.candle_opened_at,
        store=store,
        clock=lambda: MOMENT,
    )
    assert report.created == ()
    assert report.skipped_existing == 1
    record = store.load_record(existing.delivery_id)
    assert record is not None and record.state is OutboxState.WAITING


def test_quarantined_history_marks_recreated_intent_ambiguous(
    tmp_path: Path, signal_frames_fixture
) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    frame = buy_frames(signal_frames_fixture)[0]
    delivery_id = expected_delivery_id(frame, DESTINATION)
    quarantine = (
        tmp_path / "outbox" / "corrupt" / (key_digest(delivery_id) + RECORD_SUFFIX + CORRUPT_SUFFIX)
    )
    quarantine.write_text("previous bytes", encoding="utf-8")
    report = reconcile_missing(
        [frame],
        DESTINATION,
        activated_at=frame.candidate.signal.candle_opened_at,
        store=store,
        clock=lambda: MOMENT,
    )
    assert report.created == (delivery_id,)
    record = store.load_record(delivery_id)
    assert record is not None
    assert record.ambiguous_attempt_seen is True
    assert record.possibly_duplicated is False


def test_reconciliation_is_idempotent_and_deterministic(
    tmp_path: Path, signal_frames_fixture
) -> None:
    first_store = FileOutboxStore(tmp_path / "one")
    second_store = FileOutboxStore(tmp_path / "two")
    buys = buy_frames(signal_frames_fixture)
    activation = buys[0].candidate.signal.candle_opened_at
    first = reconcile_missing(
        signal_frames_fixture,
        DESTINATION,
        activated_at=activation,
        store=first_store,
        clock=lambda: MOMENT,
    )
    second = reconcile_missing(
        signal_frames_fixture,
        DESTINATION,
        activated_at=activation,
        store=second_store,
        clock=lambda: MOMENT,
    )
    assert first.created == second.created
    # Re-running over the same store creates nothing new.
    rerun = reconcile_missing(
        signal_frames_fixture,
        DESTINATION,
        activated_at=activation,
        store=first_store,
        clock=lambda: MOMENT,
    )
    assert rerun.created == ()
    assert rerun.skipped_existing == len(buys)


def test_interrupted_reconciliation_completes_on_restart(
    tmp_path: Path, signal_frames_fixture
) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    buys = buy_frames(signal_frames_fixture)
    activation = buys[0].candidate.signal.candle_opened_at
    # Crash after reconciling only the first BUY frame.
    partial = reconcile_missing(
        buys[:1],
        DESTINATION,
        activated_at=activation,
        store=store,
        clock=lambda: MOMENT,
    )
    assert partial.created_count == 1
    # Restart reconciles the full window: the remainder appears, no duplicates.
    full = reconcile_missing(
        signal_frames_fixture,
        DESTINATION,
        activated_at=activation,
        store=store,
        clock=lambda: MOMENT,
    )
    assert full.created == tuple(expected_delivery_id(f, DESTINATION) for f in buys[1:])
    assert full.skipped_existing == 1
    all_records = store.all_records()
    assert len(all_records) == len(buys)
    assert len({record.delivery_id for record in all_records}) == len(buys)


def test_budget_configuration_flows_into_recreated_intents(
    tmp_path: Path, signal_frames_fixture
) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    frame = buy_frames(signal_frames_fixture)[0]
    config = OutboxConfig(max_attempts=7)
    reconcile_missing(
        [frame],
        DESTINATION,
        activated_at=frame.candidate.signal.candle_opened_at,
        store=store,
        config=config,
        clock=lambda: MOMENT,
    )
    record = store.load_record(expected_delivery_id(frame, DESTINATION))
    assert record is not None and record.max_attempts == 7


def test_validation(tmp_path: Path, signal_frames_fixture) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    frame = buy_frames(signal_frames_fixture)[0]
    with pytest.raises(AnalysisInputError):
        reconcile_missing(
            [frame],
            "",
            activated_at=MOMENT,
            store=store,  # empty destination
        )
    with pytest.raises(AnalysisInputError):
        reconcile_missing(
            [object()],  # type: ignore[list-item]
            DESTINATION,
            activated_at=MOMENT,
            store=store,
        )
