"""Phase 27 round-trip: declared datasets persist and restore exactly.

Every restored dataset must equal the saved one field-for-field through the
frozen constructors, and a restored dataset must drive the identical Phase
26H run — proving the durable history is the same history the ledger was
verified against.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smcsignal.data import OHLCV
from smcsignal.datasets import (
    FileDatasetStore,
    MemoryDatasetStore,
    dataset_bytes,
    load_dataset_bytes,
)
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from smcsignal.runs import run_declared_history
from tests.backtest.helpers import configuration, dataset
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL
from tests.runs.test_series_run import assert_ledgers_equal, uninterrupted

KEY = "series-primary"
DATASET_KEY = "dataset-primary"
PIPELINE = configuration()

SCENARIOS = (
    ("rising", tuple(range(20, 37))),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)


def variants() -> tuple:
    return (
        ("default", dataset()),
        ("other_symbol", dataset(symbol="ETHUSDT")),
        ("other_dataset_id", dataset(dataset_id="phase27-fixture:v2")),
        ("no_htf_histories", dataset(higher={})),
        ("rise_then_fall", dataset(prices=RISE_THEN_FALL)),
        ("chop_only", dataset(prices=CHOP_ONLY)),
    )


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileDatasetStore(tmp_path / "datasets")
    return MemoryDatasetStore()


@pytest.mark.parametrize(("label", "original"), variants(), ids=[v[0] for v in variants()])
def test_declared_dataset_round_trips_exactly(store, label, original) -> None:
    store.save(DATASET_KEY, original)
    assert store.contains(DATASET_KEY)
    restored = store.load(DATASET_KEY)

    assert restored == original
    assert restored is not None
    assert restored.symbol == original.symbol
    assert restored.timeframe == original.timeframe
    assert restored.venue == original.venue
    assert restored.provider == original.provider
    assert restored.dataset_id == original.dataset_id
    assert restored.candles == original.candles
    assert dict(restored.higher_candles) == dict(original.higher_candles)
    assert dataset_bytes(restored) == dataset_bytes(original)


def test_htf_histories_round_trip_with_every_timeframe(store) -> None:
    original = dataset()
    store.save(DATASET_KEY, original)
    restored = store.load(DATASET_KEY)
    assert restored is not None
    assert set(restored.higher_candles) == {"1h", "4h"}
    for timeframe in ("1h", "4h"):
        assert restored.higher_candles[timeframe] == original.higher_candles[timeframe]


def test_candle_values_round_trip_losslessly(store) -> None:
    original = dataset()
    store.save(DATASET_KEY, original)
    restored = store.load(DATASET_KEY)
    assert restored is not None
    first = restored.candles[0]
    assert first.timestamp == datetime(2024, 1, 1, 8, tzinfo=UTC)
    assert first.open == Decimal(20) and first.close == Decimal(20)
    assert first.high == Decimal(21) and first.low == Decimal(19)
    assert first.volume == Decimal(1)
    assert isinstance(first, OHLCV)


def test_memory_and_file_stores_agree_on_protocol_behavior(tmp_path) -> None:
    rising = dataset()
    falling = dataset(symbol="ETHUSDT", prices=tuple(range(36, 19, -1)))
    memory, file_store = MemoryDatasetStore(), FileDatasetStore(tmp_path / "datasets")

    for candidate in (memory, file_store):
        assert candidate.load("missing") is None
        assert candidate.contains("missing") is False
        candidate.save("alpha", rising)
        candidate.save("beta", falling)

    assert memory.load("alpha") == file_store.load("alpha") == rising
    assert memory.load("beta") == file_store.load("beta") == falling
    assert memory.contains("alpha") is True and file_store.contains("alpha") is True


def test_missing_key_loads_nothing_and_contains_reflects_presence(store) -> None:
    assert store.load("never-saved") is None
    assert store.contains("never-saved") is False
    store.save("present", dataset())
    assert store.contains("present") is True
    assert store.load("never-saved") is None


def test_distinct_keys_remain_isolated(store) -> None:
    rising = dataset()
    falling = dataset(symbol="ETHUSDT", prices=tuple(range(36, 19, -1)))
    store.save("dataset-one", rising)
    store.save("dataset-two", falling)
    assert store.load("dataset-one") == rising
    assert store.load("dataset-two") == falling
    assert dataset_bytes(store.load("dataset-one")) != dataset_bytes(store.load("dataset-two"))


def test_repeated_identical_saves_are_deterministic(tmp_path) -> None:
    store = FileDatasetStore(tmp_path / "datasets")
    original = dataset()
    store.save(DATASET_KEY, original)
    first_bytes = (tmp_path / "datasets" / f"{DATASET_KEY}.dataset.json").read_bytes()
    store.save(DATASET_KEY, original)
    assert (tmp_path / "datasets" / f"{DATASET_KEY}.dataset.json").read_bytes() == first_bytes
    assert first_bytes == dataset_bytes(original)
    assert store.load(DATASET_KEY) == original


def test_changed_dataset_replaces_the_previous_one(store) -> None:
    before = dataset()
    after = dataset(symbol="ETHUSDT")
    assert dataset_bytes(before) != dataset_bytes(after)
    store.save(DATASET_KEY, before)
    assert store.load(DATASET_KEY) == before
    store.save(DATASET_KEY, after)
    assert store.load(DATASET_KEY) == after


def test_file_store_creates_the_root_on_first_save_and_reads_none_without_it(tmp_path) -> None:
    root = tmp_path / "does" / "not" / "exist" / "yet"
    store = FileDatasetStore(root)
    assert store.load(DATASET_KEY) is None and not store.contains(DATASET_KEY)
    store.save(DATASET_KEY, dataset())
    assert store.contains(DATASET_KEY)
    assert store.load(DATASET_KEY) == dataset()


def test_one_validated_key_maps_to_exactly_one_file_under_the_root(tmp_path) -> None:
    store = FileDatasetStore(tmp_path / "datasets")
    store.save("dataset-a", dataset())
    store.save("dataset-b", dataset(symbol="ETHUSDT"))
    names = sorted(path.name for path in (tmp_path / "datasets").iterdir())
    assert names == ["dataset-a.dataset.json", "dataset-b.dataset.json"]


def test_determinism_across_independent_file_stores(tmp_path) -> None:
    original = dataset()
    store_a = FileDatasetStore(tmp_path / "root-a")
    store_b = FileDatasetStore(tmp_path / "root-b")
    store_a.save(DATASET_KEY, original)
    store_b.save(DATASET_KEY, original)
    bytes_a = (tmp_path / "root-a" / f"{DATASET_KEY}.dataset.json").read_bytes()
    bytes_b = (tmp_path / "root-b" / f"{DATASET_KEY}.dataset.json").read_bytes()
    assert bytes_a == bytes_b == dataset_bytes(original)
    assert store_a.load(DATASET_KEY) == store_b.load(DATASET_KEY) == original


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_a_restored_dataset_drives_the_identical_26h_run(store, label, prices) -> None:
    original = dataset(prices=prices)
    store.save(DATASET_KEY, original)
    restored = store.load(DATASET_KEY)
    assert restored is not None

    ledger_store = MemoryLedgerStore()
    ran = run_declared_history(restored, PIPELINE, ledger_store, KEY)
    full = uninterrupted(prices)

    assert_ledgers_equal(ran.lifecycle, full)
    assert ran.frames_verified == len(original.candles)
    assert ran.config == PIPELINE.outcome_tracking


def test_restart_through_the_dataset_store_matches_uninterrupted(tmp_path) -> None:
    """Full restart story: history comes from the dataset store both times."""

    dataset_store = FileDatasetStore(tmp_path / "datasets")
    dataset_store.save(DATASET_KEY, dataset())

    ledger_root = tmp_path / "ledgers"
    first_history = dataset_store.load(DATASET_KEY)
    assert first_history is not None
    first_run = run_declared_history(first_history, PIPELINE, FileLedgerStore(ledger_root), KEY)
    first_run.persist()
    assert first_run.recovered_from is None

    # Restart: a fresh store instance over the same roots re-supplies the
    # exact declared history and recovers the durable session.
    restarted_history = FileDatasetStore(tmp_path / "datasets").load(DATASET_KEY)
    assert restarted_history is not None and restarted_history == dataset()
    restarted = run_declared_history(restarted_history, PIPELINE, FileLedgerStore(ledger_root), KEY)

    assert_ledgers_equal(restarted.lifecycle, first_run.lifecycle)
    assert restarted.recovered_from is not None
    assert restarted.recovered_from == first_run.persist()
    assert_ledgers_equal(restarted.lifecycle, uninterrupted(tuple(range(20, 37))))


def test_dataset_bytes_round_trip_is_lossless() -> None:
    original = dataset()
    restored = load_dataset_bytes(dataset_bytes(original))
    assert restored == original
    assert dataset_bytes(restored) == dataset_bytes(original)
