"""Phase 26D round-trip: real lifecycle ledgers persist and restore exactly.

Every ledger is built by the real Phase 26B lifecycle over the real Phase 3-17
chain. A stored ledger must equal the live one in observations, OPEN outcomes,
finalized outcomes, StrategyStats, MonthlyReport, canonical bytes, and
snapshot identity — through the Phase 26C API alone.
"""

from __future__ import annotations

import pytest

from smcsignal.analytics import RestoredAnalyticsLedger, ledger_bytes, snapshot_ledger
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from tests.analytics.test_ledger import SCENARIOS, lifecycle_for
from tests.outcome_tracking.helpers import FALLING_TAIL


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileLedgerStore(tmp_path / "ledgers")
    return MemoryLedgerStore()


@pytest.mark.parametrize(("label", "candles", "horizon"), SCENARIOS)
def test_real_lifecycle_ledger_round_trips_through_the_store(store, label, candles, horizon):
    lifecycle = lifecycle_for(label, candles, horizon)
    snapshot = snapshot_ledger(lifecycle)

    store.save("series-primary", snapshot)
    assert store.contains("series-primary")
    restored_snapshot = store.load("series-primary")
    assert restored_snapshot == snapshot
    assert restored_snapshot is not None and restored_snapshot.snapshot_id == snapshot.snapshot_id

    restored = RestoredAnalyticsLedger(restored_snapshot)
    observer = lifecycle.observer
    assert restored.observations == observer.observations
    assert restored.open_outcomes == observer.open_outcomes
    assert restored.finalized_outcomes == observer.finalized_outcomes
    assert restored.strategy_stats == observer.strategy_stats
    assert restored.monthly_report() == observer.monthly_report()
    assert restored.settings == observer.config
    if label != "empty":
        assert ledger_bytes(restored_snapshot) == ledger_bytes(snapshot)


def test_memory_and_file_stores_agree_on_protocol_behavior(tmp_path):
    win = snapshot_ledger(lifecycle_for("win"))
    loss = snapshot_ledger(lifecycle_for("loss", FALLING_TAIL))
    memory, file_store = MemoryLedgerStore(), FileLedgerStore(tmp_path / "ledgers")

    for store in (memory, file_store):
        assert store.load("missing") is None
        assert store.contains("missing") is False
        store.save("alpha", win)
        store.save("beta", loss)

    assert memory.load("alpha") == file_store.load("alpha") == win
    assert memory.load("beta") == file_store.load("beta") == loss
    assert memory.contains("alpha") is True and file_store.contains("alpha") is True


def test_missing_key_loads_nothing_and_contains_reflects_presence(store):
    assert store.load("never-saved") is None
    assert store.contains("never-saved") is False
    store.save("present", snapshot_ledger(lifecycle_for("empty")))
    assert store.contains("present") is True
    assert store.load("never-saved") is None


def test_distinct_keys_remain_isolated(store):
    win = snapshot_ledger(lifecycle_for("win"))
    loss = snapshot_ledger(lifecycle_for("loss", FALLING_TAIL))
    store.save("series-one", win)
    store.save("series-two", loss)
    assert store.load("series-one") == win
    assert store.load("series-two") == loss
    assert store.load("series-one").snapshot_id != store.load("series-two").snapshot_id


def test_repeated_identical_saves_are_deterministic(tmp_path):
    store = FileLedgerStore(tmp_path / "ledgers")
    snapshot = snapshot_ledger(lifecycle_for("win"))
    store.save("series", snapshot)
    first_bytes = (tmp_path / "ledgers" / "series.ledger.json").read_bytes()
    store.save("series", snapshot)
    assert (tmp_path / "ledgers" / "series.ledger.json").read_bytes() == first_bytes
    assert first_bytes == ledger_bytes(snapshot)
    assert store.load("series") == snapshot


def test_changed_snapshot_replaces_the_previous_one(store):
    before = snapshot_ledger(lifecycle_for("win"))
    after = snapshot_ledger(lifecycle_for("loss", FALLING_TAIL))
    assert before.snapshot_id != after.snapshot_id
    store.save("series", before)
    assert store.load("series") == before
    store.save("series", after)
    assert store.load("series") == after
    assert store.load("series").snapshot_id == after.snapshot_id


def test_file_store_creates_the_root_on_first_save_and_reads_none_without_it(tmp_path):
    root = tmp_path / "does" / "not" / "exist" / "yet"
    store = FileLedgerStore(root)
    assert store.load("series") is None and not store.contains("series")
    store.save("series", snapshot_ledger(lifecycle_for("breakeven")))
    assert store.contains("series")
    assert store.load("series") == snapshot_ledger(lifecycle_for("breakeven"))


def test_one_validated_key_maps_to_exactly_one_file_under_the_root(tmp_path):
    store = FileLedgerStore(tmp_path / "ledgers")
    store.save("series-a", snapshot_ledger(lifecycle_for("win")))
    store.save("series-b", snapshot_ledger(lifecycle_for("loss", FALLING_TAIL)))
    names = sorted(path.name for path in (tmp_path / "ledgers").iterdir())
    assert names == ["series-a.ledger.json", "series-b.ledger.json"]
