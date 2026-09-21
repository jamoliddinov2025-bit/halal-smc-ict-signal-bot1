"""Phase 29 round-trip: declared-run bindings persist and restore exactly.

A restored binding must equal the saved one field-for-field, preserve the
Phase 27 dataset ``content_digest`` and Phase 28 ``configuration_digest``
pins, and — on the caller side — drive the identical Phase 26H run from the
three existing stores addressed by the restored keys.
"""

from __future__ import annotations

import json

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.configurations import (
    FileConfigurationStore,
    configuration_bytes,
)
from smcsignal.datasets import FileDatasetStore, dataset_bytes
from smcsignal.declarations import (
    DeclaredRunBinding,
    FileRunBindingStore,
    MemoryRunBindingStore,
    binding_bytes,
    load_binding_bytes,
)
from smcsignal.persistence import FileLedgerStore
from smcsignal.runs import run_declared_history
from tests.backtest.helpers import configuration, dataset
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL
from tests.runs.test_series_run import assert_ledgers_equal, uninterrupted

RUN_KEY = "run-primary"
DATASET_KEY = "dataset-primary"
CONFIGURATION_KEY = "pipeline-default"
LEDGER_KEY = "series-primary"
PIPELINE = configuration()

SCENARIOS = (
    ("rising", tuple(range(20, 37))),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)


def phase27_dataset_digest(history) -> str:
    """The canonical Phase 27 dataset identity: document ``content_digest``."""

    digest = json.loads(dataset_bytes(history))["content_digest"]
    assert isinstance(digest, str)
    return digest


def phase28_configuration_digest(pipeline) -> str:
    """The canonical Phase 28 configuration identity: ``configuration_digest``."""

    digest = json.loads(configuration_bytes(pipeline))["configuration_digest"]
    assert isinstance(digest, str)
    return digest


def binding_for(
    history=None,
    pipeline=None,
    *,
    dataset_key: str = DATASET_KEY,
    configuration_key: str = CONFIGURATION_KEY,
    ledger_key: str = LEDGER_KEY,
) -> DeclaredRunBinding:
    history = dataset() if history is None else history
    pipeline = PIPELINE if pipeline is None else pipeline
    return DeclaredRunBinding(
        dataset_key=dataset_key,
        configuration_key=configuration_key,
        ledger_key=ledger_key,
        dataset_digest=phase27_dataset_digest(history),
        configuration_digest=phase28_configuration_digest(pipeline),
    )


def variants() -> tuple:
    return (
        ("helpers_fixtures", binding_for()),
        (
            "other_dataset",
            binding_for(dataset(symbol="ETHUSDT"), dataset_key="dataset-eth"),
        ),
        (
            "other_configuration",
            binding_for(
                pipeline=configuration(threshold=15, horizon=4),
                configuration_key="pipeline-strict",
            ),
        ),
        (
            "other_ledger_key",
            binding_for(ledger_key="series-alt"),
        ),
    )


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileRunBindingStore(tmp_path / "declarations")
    return MemoryRunBindingStore()


def test_declared_run_binding_is_frozen() -> None:
    original = binding_for()
    with pytest.raises(AttributeError):
        original.dataset_key = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize(("label", "original"), variants(), ids=[v[0] for v in variants()])
def test_declared_run_binding_round_trips_exactly(store, label, original) -> None:
    store.save(RUN_KEY, original)
    assert store.contains(RUN_KEY)
    restored = store.load(RUN_KEY)

    assert restored == original
    assert restored is not None
    assert restored.dataset_key == original.dataset_key
    assert restored.configuration_key == original.configuration_key
    assert restored.ledger_key == original.ledger_key
    assert restored.dataset_digest == original.dataset_digest
    assert restored.configuration_digest == original.configuration_digest
    assert binding_bytes(restored) == binding_bytes(original)


@pytest.mark.parametrize(("label", "original"), variants(), ids=[v[0] for v in variants()])
def test_content_pins_are_the_phase27_and_phase28_identities(original, label) -> None:
    restored = load_binding_bytes(binding_bytes(original))
    assert restored.dataset_digest.startswith("dataset:")
    assert restored.configuration_digest.startswith("configuration:")
    assert restored.dataset_digest == original.dataset_digest
    assert restored.configuration_digest == original.configuration_digest


def test_memory_and_file_stores_agree_on_protocol_behavior(tmp_path) -> None:
    primary = binding_for()
    other = binding_for(dataset(symbol="ETHUSDT"), dataset_key="dataset-eth")
    memory = MemoryRunBindingStore()
    file_store = FileRunBindingStore(tmp_path / "declarations")

    for candidate in (memory, file_store):
        assert candidate.load("missing") is None
        assert candidate.contains("missing") is False
        candidate.save("alpha", primary)
        candidate.save("beta", other)

    assert memory.load("alpha") == file_store.load("alpha") == primary
    assert memory.load("beta") == file_store.load("beta") == other
    assert memory.contains("alpha") is True and file_store.contains("alpha") is True


def test_missing_key_loads_nothing_and_contains_reflects_presence(store) -> None:
    assert store.load("never-saved") is None
    assert store.contains("never-saved") is False
    store.save("present", binding_for())
    assert store.contains("present") is True
    assert store.load("never-saved") is None


def test_distinct_keys_remain_isolated(store) -> None:
    primary = binding_for()
    other = binding_for(dataset(symbol="ETHUSDT"), dataset_key="dataset-eth")
    assert binding_bytes(primary) != binding_bytes(other)
    store.save("run-one", primary)
    store.save("run-two", other)
    assert store.load("run-one") == primary
    assert store.load("run-two") == other


def test_repeated_identical_saves_are_deterministic(tmp_path) -> None:
    store = FileRunBindingStore(tmp_path / "declarations")
    original = binding_for()
    store.save(RUN_KEY, original)
    target = tmp_path / "declarations" / f"{RUN_KEY}.binding.json"
    first_bytes = target.read_bytes()
    store.save(RUN_KEY, original)
    assert target.read_bytes() == first_bytes
    assert first_bytes == binding_bytes(original)
    assert store.load(RUN_KEY) == original


def test_changed_binding_replaces_the_previous_one(store) -> None:
    before = binding_for()
    after = binding_for(dataset(symbol="ETHUSDT"), dataset_key="dataset-eth")
    assert binding_bytes(before) != binding_bytes(after)
    store.save(RUN_KEY, before)
    assert store.load(RUN_KEY) == before
    store.save(RUN_KEY, after)
    assert store.load(RUN_KEY) == after


def test_file_store_creates_the_root_on_first_save_and_reads_none_without_it(tmp_path) -> None:
    root = tmp_path / "does" / "not" / "exist" / "yet"
    store = FileRunBindingStore(root)
    assert store.load(RUN_KEY) is None and not store.contains(RUN_KEY)
    store.save(RUN_KEY, binding_for())
    assert store.contains(RUN_KEY)
    assert store.load(RUN_KEY) == binding_for()


def test_one_validated_key_maps_to_exactly_one_file_under_the_root(tmp_path) -> None:
    store = FileRunBindingStore(tmp_path / "declarations")
    store.save("run-a", binding_for())
    store.save("run-b", binding_for(ledger_key="series-alt"))
    names = sorted(path.name for path in (tmp_path / "declarations").iterdir())
    assert names == ["run-a.binding.json", "run-b.binding.json"]


def test_determinism_across_independent_file_stores(tmp_path) -> None:
    original = binding_for()
    store_a = FileRunBindingStore(tmp_path / "root-a")
    store_b = FileRunBindingStore(tmp_path / "root-b")
    store_a.save(RUN_KEY, original)
    store_b.save(RUN_KEY, original)
    bytes_a = (tmp_path / "root-a" / f"{RUN_KEY}.binding.json").read_bytes()
    bytes_b = (tmp_path / "root-b" / f"{RUN_KEY}.binding.json").read_bytes()
    assert bytes_a == bytes_b == binding_bytes(original)
    assert store_a.load(RUN_KEY) == store_b.load(RUN_KEY) == original


def test_binding_bytes_round_trip_is_lossless() -> None:
    original = binding_for()
    restored = load_binding_bytes(binding_bytes(original))
    assert restored == original
    assert binding_bytes(restored) == binding_bytes(original)


def test_constructor_rejects_invalid_identity_strings() -> None:
    with pytest.raises(AnalysisInputError, match="dataset:"):
        DeclaredRunBinding(
            dataset_key=DATASET_KEY,
            configuration_key=CONFIGURATION_KEY,
            ledger_key=LEDGER_KEY,
            dataset_digest="not-a-phase-27-digest",
            configuration_digest=phase28_configuration_digest(PIPELINE),
        )
    with pytest.raises(AnalysisInputError, match="configuration:"):
        DeclaredRunBinding(
            dataset_key=DATASET_KEY,
            configuration_key=CONFIGURATION_KEY,
            ledger_key=LEDGER_KEY,
            dataset_digest=phase27_dataset_digest(dataset()),
            configuration_digest="configuration:zzzz",
        )


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_restart_through_the_binding_matches_uninterrupted(tmp_path, label, prices) -> None:
    """Caller-side restart: binding keys address the three existing stores."""

    history = dataset(prices=prices)
    pipeline = PIPELINE
    declaration = binding_for(history, pipeline)

    FileDatasetStore(tmp_path / "datasets").save(declaration.dataset_key, history)
    FileConfigurationStore(tmp_path / "configurations").save(
        declaration.configuration_key, pipeline
    )
    FileRunBindingStore(tmp_path / "declarations").save(RUN_KEY, declaration)

    first_history = FileDatasetStore(tmp_path / "datasets").load(declaration.dataset_key)
    first_configuration = FileConfigurationStore(tmp_path / "configurations").load(
        declaration.configuration_key
    )
    assert first_history is not None and first_configuration is not None
    first_run = run_declared_history(
        first_history, first_configuration, FileLedgerStore(tmp_path / "ledgers"), LEDGER_KEY
    )
    first_run.persist()
    assert first_run.recovered_from is None

    restored = FileRunBindingStore(tmp_path / "declarations").load(RUN_KEY)
    assert restored == declaration
    restarted_history = FileDatasetStore(tmp_path / "datasets").load(restored.dataset_key)
    restarted_configuration = FileConfigurationStore(tmp_path / "configurations").load(
        restored.configuration_key
    )
    assert restarted_history is not None and restarted_configuration is not None
    assert phase27_dataset_digest(restarted_history) == restored.dataset_digest
    assert phase28_configuration_digest(restarted_configuration) == restored.configuration_digest

    restarted = run_declared_history(
        restarted_history,
        restarted_configuration,
        FileLedgerStore(tmp_path / "ledgers"),
        restored.ledger_key,
    )
    assert_ledgers_equal(restarted.lifecycle, first_run.lifecycle)
    assert restarted.recovered_from is not None
    assert restarted.recovered_from == first_run.persist()
    assert_ledgers_equal(restarted.lifecycle, uninterrupted(prices))


def test_replaced_dataset_under_the_same_key_fails_closed_at_26h(tmp_path) -> None:
    """Phase 29 does not weaken 26E/26G: a replaced history still refuses recovery."""

    history = dataset()
    declaration = binding_for(history)
    FileDatasetStore(tmp_path / "datasets").save(declaration.dataset_key, history)
    FileConfigurationStore(tmp_path / "configurations").save(
        declaration.configuration_key, PIPELINE
    )
    FileRunBindingStore(tmp_path / "declarations").save(RUN_KEY, declaration)
    ledger_store = FileLedgerStore(tmp_path / "ledgers")
    first = run_declared_history(history, PIPELINE, ledger_store, LEDGER_KEY)
    first.persist()
    before_snapshot = first.persist()

    FileDatasetStore(tmp_path / "datasets").save(declaration.dataset_key, dataset(symbol="ETHUSDT"))
    restored = FileRunBindingStore(tmp_path / "declarations").load(RUN_KEY)
    assert restored is not None
    replaced = FileDatasetStore(tmp_path / "datasets").load(restored.dataset_key)
    pipeline = FileConfigurationStore(tmp_path / "configurations").load(restored.configuration_key)
    assert replaced is not None and pipeline is not None
    assert phase27_dataset_digest(replaced) != restored.dataset_digest

    with pytest.raises(AnalysisInputError):
        run_declared_history(
            replaced,
            pipeline,
            FileLedgerStore(tmp_path / "ledgers"),
            restored.ledger_key,
        )
    assert FileLedgerStore(tmp_path / "ledgers").load(LEDGER_KEY) == before_snapshot
