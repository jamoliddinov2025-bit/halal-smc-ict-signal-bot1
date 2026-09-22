"""Phase 35D unit tests: the durable file store (atomicity, quarantine, meta)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.delivery.outbox.helpers import project_payload

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.outbox.models import new_queued_record
from smcsignal.delivery.outbox.store import (
    CORRUPT_SUFFIX,
    META_NAME,
    RECORD_SUFFIX,
    FileOutboxStore,
    OutboxStoreError,
    key_digest,
)

MOMENT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)


def make_record(frame, *, now: datetime = MOMENT):
    return new_queued_record(project_payload(frame), now=now, max_attempts=5)


def test_key_digest_validates_the_frozen_identity_shape(buy_snapshot) -> None:
    record = make_record(buy_snapshot)
    digest = key_digest(record.delivery_id)
    assert len(digest) == 64
    assert all(character in "0123456789abcdef" for character in digest)


@pytest.mark.parametrize(
    "bad_key",
    [
        "message:" + "a" * 64,
        "delivery:" + "A" * 64,  # uppercase is not the digest charset
        "delivery:" + "g" * 64,  # non-hex
        "delivery:" + "a" * 63,  # wrong length
        "delivery:" + "a" * 65,
        "delivery:",
        "delivery:xyz",
        42,
    ],
)
def test_key_digest_rejects_everything_else(bad_key: object) -> None:
    with pytest.raises(AnalysisInputError):
        key_digest(bad_key)  # type: ignore[arg-type]


def test_layout_created_and_partials_swept(tmp_path: Path) -> None:
    root = tmp_path / "outbox"
    root.mkdir()
    stale = root / "records"
    stale.mkdir()
    leftover = stale / ("a" * 64 + RECORD_SUFFIX + ".partial")
    leftover.write_text("partial", encoding="utf-8")
    store = FileOutboxStore(root)
    assert (root / "records").is_dir()
    assert (root / "corrupt").is_dir()
    assert not leftover.exists()
    assert store.root == root


def test_save_load_round_trip_and_no_partial_residue(tmp_path: Path, buy_snapshot) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    record = make_record(buy_snapshot)
    store.save_record(record)
    assert store.load_record(record.delivery_id) == record
    leftovers = list((tmp_path / "outbox" / "records").glob("*.partial"))
    assert leftovers == []


def test_load_missing_record_is_none(tmp_path: Path) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    assert store.load_record("delivery:" + "b" * 64) is None


def test_corrupt_record_quarantined_and_counted(tmp_path: Path, buy_snapshot) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    record = make_record(buy_snapshot)
    path = tmp_path / "outbox" / "records" / (key_digest(record.delivery_id) + RECORD_SUFFIX)
    path.write_text("{not json", encoding="utf-8")
    assert store.load_record(record.delivery_id) is None
    assert store.quarantine_count == 1
    quarantined = tmp_path / "outbox" / "corrupt" / (path.name + CORRUPT_SUFFIX)
    assert quarantined.exists()
    assert not path.exists()
    assert store.quarantined_ids() == frozenset({record.delivery_id})


def test_schema_mismatch_quarantined(tmp_path: Path, buy_snapshot) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    record = make_record(buy_snapshot)
    document = record.to_document()
    document["schema"] = "smcsignal.outbox.record/v9"
    path = tmp_path / "outbox" / "records" / (key_digest(record.delivery_id) + RECORD_SUFFIX)
    path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load_record(record.delivery_id) is None
    assert store.quarantine_count == 1


def test_key_content_mismatch_quarantined(tmp_path: Path, buy_snapshot) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    record = make_record(buy_snapshot)
    document = record.to_document()
    document["delivery_id"] = "delivery:" + "c" * 64
    path = tmp_path / "outbox" / "records" / (key_digest(record.delivery_id) + RECORD_SUFFIX)
    path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load_record(record.delivery_id) is None
    assert store.quarantine_count == 1


def test_all_records_deterministic_order_and_quarantine_exclusion(
    tmp_path: Path, signal_frames_fixture
) -> None:
    from smcsignal.analysis.signal_engine.models import SignalStatus

    buys = [f for f in signal_frames_fixture if f.status is SignalStatus.BUY_SIGNAL][:3]
    store = FileOutboxStore(tmp_path / "outbox")
    records = [make_record(frame) for frame in buys]
    for record in records:
        store.save_record(record)
    # Corrupt the middle record: it must drop out deterministically.
    middle = sorted(records, key=lambda item: item.delivery_id)[1]
    path = tmp_path / "outbox" / "records" / (key_digest(middle.delivery_id) + RECORD_SUFFIX)
    path.write_text("corrupt", encoding="utf-8")
    loaded = store.all_records()
    expected_ids = sorted(r.delivery_id for r in records)
    expected_ids.remove(middle.delivery_id)
    assert [record.delivery_id for record in loaded] == expected_ids
    assert store.quarantine_count == 1


def test_ensure_meta_creates_once_and_is_stable(tmp_path: Path) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    assert store.meta_failure is None
    activated = store.ensure_meta(MOMENT)
    assert activated == MOMENT
    # A later moment must not move the persisted activation boundary.
    assert store.ensure_meta(LATER) == MOMENT
    document = json.loads((tmp_path / "outbox" / META_NAME).read_text(encoding="utf-8"))
    assert document["schema"] == "smcsignal.outbox.meta/v1"


def test_no_meta_written_before_bootstrap(tmp_path: Path) -> None:
    FileOutboxStore(tmp_path / "outbox")
    assert not (tmp_path / "outbox" / META_NAME).exists()


def test_corrupt_meta_fails_closed(tmp_path: Path) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    (tmp_path / "outbox" / META_NAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(OutboxStoreError):
        store.ensure_meta(MOMENT)
    assert store.meta_failure is not None
    # The untrusted marker is quarantined, never silently rewritten.
    assert not (tmp_path / "outbox" / META_NAME).exists()
    quarantined = list((tmp_path / "outbox" / "corrupt").glob(f"{META_NAME}{CORRUPT_SUFFIX}"))
    assert len(quarantined) == 1


def test_wrong_schema_meta_fails_closed(tmp_path: Path) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    (tmp_path / "outbox" / META_NAME).write_text(
        json.dumps(
            {"schema": "smcsignal.outbox.meta/v9", "activated_at": "2026-01-01T00:00:00+00:00"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(OutboxStoreError):
        store.ensure_meta(MOMENT)
    assert store.meta_failure is not None


def test_atomic_replacement_keeps_previous_on_bad_write(tmp_path: Path, buy_snapshot) -> None:
    store = FileOutboxStore(tmp_path / "outbox")
    record = make_record(buy_snapshot)
    store.save_record(record)
    # A failed save (invalid record type) must leave the previous bytes intact.
    with pytest.raises(AnalysisInputError):
        store.save_record(None)  # type: ignore[arg-type]
    assert store.load_record(record.delivery_id) == record
