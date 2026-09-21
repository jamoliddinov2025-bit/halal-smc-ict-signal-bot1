"""Phase 30 materialization: a binding resolves to its verified declared inputs.

A valid Phase 29 binding over the real Phase 27/28 stores materializes into
one frozen ``VerifiedRunInputs`` whose dataset and configuration equal the
values that were declared, whose canonical Phase 27/28 identities equal the
identities the binding pinned, and whose ``ledger_key`` is the binding's.
Materialization is deterministic across repetitions, independent store
instances, and restart-equivalent reloads — and it ends there: the caller
composes the verified inputs into the frozen Phase 26H seam itself.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.configurations import (
    FileConfigurationStore,
    MemoryConfigurationStore,
    configuration_bytes,
)
from smcsignal.datasets import FileDatasetStore, MemoryDatasetStore, dataset_bytes
from smcsignal.declarations import (
    DeclaredRunBinding,
    FileRunBindingStore,
    MemoryRunBindingStore,
    binding_bytes,
)
from smcsignal.materialization import VerifiedRunInputs, materialize_declared_inputs
from smcsignal.persistence import FileLedgerStore
from smcsignal.runs import run_declared_history
from tests.backtest.helpers import configuration, dataset
from tests.declarations.test_run_binding import (
    CONFIGURATION_KEY,
    DATASET_KEY,
    LEDGER_KEY,
    PIPELINE,
    RUN_KEY,
    binding_for,
    phase27_dataset_digest,
    phase28_configuration_digest,
)
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL
from tests.runs.test_series_run import assert_ledgers_equal, uninterrupted

SCENARIOS = (
    ("rising", tuple(range(20, 37))),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)


class Stores:
    """The three existing stores a caller hands to Phase 30, file or memory backed."""

    def __init__(self, kind: str, root) -> None:
        self.kind = kind
        self.root = root
        if kind == "file":
            self.datasets = FileDatasetStore(root / "datasets")
            self.configurations = FileConfigurationStore(root / "configurations")
            self.bindings = FileRunBindingStore(root / "declarations")
        else:
            self.datasets = MemoryDatasetStore()
            self.configurations = MemoryConfigurationStore()
            self.bindings = MemoryRunBindingStore()

    def declare(
        self,
        history=None,
        pipeline=None,
        *,
        run_key: str = RUN_KEY,
        dataset_key: str = DATASET_KEY,
        configuration_key: str = CONFIGURATION_KEY,
        ledger_key: str = LEDGER_KEY,
    ) -> DeclaredRunBinding:
        """Save a dataset, a configuration, and the binding that pins them."""

        history = dataset() if history is None else history
        pipeline = PIPELINE if pipeline is None else pipeline
        declaration = binding_for(
            history,
            pipeline,
            dataset_key=dataset_key,
            configuration_key=configuration_key,
            ledger_key=ledger_key,
        )
        self.datasets.save(dataset_key, history)
        self.configurations.save(configuration_key, pipeline)
        self.bindings.save(run_key, declaration)
        return declaration

    def materialize(self, run_key: str = RUN_KEY) -> VerifiedRunInputs:
        return materialize_declared_inputs(
            self.bindings, run_key, self.datasets, self.configurations
        )

    def reopened(self) -> Stores:
        """Fresh store instances over the same roots: the restart-equivalent view."""

        return Stores(self.kind, self.root)


@pytest.fixture(params=["file", "memory"])
def stores(request, tmp_path) -> Stores:
    return Stores(request.param, tmp_path)


def test_valid_binding_materializes_verified_inputs(stores) -> None:
    declaration = stores.declare()

    inputs = stores.materialize()

    assert isinstance(inputs, VerifiedRunInputs)
    assert inputs.binding == declaration
    assert inputs.dataset == dataset()
    assert inputs.configuration == PIPELINE
    assert inputs.ledger_key == LEDGER_KEY == declaration.ledger_key


def test_materialized_dataset_identity_matches_the_binding(stores) -> None:
    declaration = stores.declare()

    inputs = stores.materialize()

    assert phase27_dataset_digest(inputs.dataset) == declaration.dataset_digest
    assert inputs.binding.dataset_digest == declaration.dataset_digest
    assert dataset_bytes(inputs.dataset) == dataset_bytes(dataset())


def test_materialized_configuration_identity_matches_the_binding(stores) -> None:
    declaration = stores.declare()

    inputs = stores.materialize()

    assert phase28_configuration_digest(inputs.configuration) == declaration.configuration_digest
    assert inputs.binding.configuration_digest == declaration.configuration_digest
    assert configuration_bytes(inputs.configuration) == configuration_bytes(PIPELINE)


def test_materialized_binding_is_the_restored_binding_exactly(stores) -> None:
    declaration = stores.declare()

    inputs = stores.materialize()

    assert binding_bytes(inputs.binding) == binding_bytes(declaration)
    assert inputs.binding.dataset_key == DATASET_KEY
    assert inputs.binding.configuration_key == CONFIGURATION_KEY
    assert inputs.binding.ledger_key == LEDGER_KEY


def test_verified_inputs_are_frozen(stores) -> None:
    stores.declare()
    inputs = stores.materialize()
    with pytest.raises(AttributeError):
        inputs.dataset = dataset(symbol="ETHUSDT")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        inputs.configuration = configuration(threshold=15)  # type: ignore[misc]
    with pytest.raises(AttributeError):
        inputs.binding = binding_for(ledger_key="series-other")  # type: ignore[misc]
    # ``ledger_key`` is a read-only view of the binding, never independent state.
    assert isinstance(VerifiedRunInputs.ledger_key, property)
    assert VerifiedRunInputs.ledger_key.fset is None
    assert inputs.ledger_key is inputs.binding.ledger_key


def test_other_datasets_configurations_and_ledger_keys_materialize(stores) -> None:
    other_history = dataset(symbol="ETHUSDT")
    other_pipeline = configuration(threshold=15, horizon=4)
    declaration = stores.declare(
        other_history,
        other_pipeline,
        dataset_key="dataset-eth",
        configuration_key="pipeline-strict",
        ledger_key="series-alt",
    )

    inputs = stores.materialize()

    assert inputs.binding == declaration
    assert inputs.dataset == other_history
    assert inputs.configuration == other_pipeline
    assert inputs.ledger_key == "series-alt"
    assert phase27_dataset_digest(inputs.dataset) == declaration.dataset_digest
    assert phase28_configuration_digest(inputs.configuration) == declaration.configuration_digest


def test_distinct_bindings_share_stores_and_materialize_independently(stores) -> None:
    primary = stores.declare(run_key="run-primary")
    strict = stores.declare(
        pipeline=configuration(threshold=15, horizon=4),
        run_key="run-strict",
        configuration_key="pipeline-strict",
        ledger_key="series-strict",
    )
    eth = stores.declare(
        dataset(symbol="ETHUSDT"),
        run_key="run-eth",
        dataset_key="dataset-eth",
        ledger_key="series-eth",
    )

    primary_inputs = stores.materialize("run-primary")
    strict_inputs = stores.materialize("run-strict")
    eth_inputs = stores.materialize("run-eth")

    assert primary_inputs.binding == primary
    assert strict_inputs.binding == strict
    assert eth_inputs.binding == eth
    # The two BTC runs share one declared dataset under one key; only the
    # configuration and ledger key differ.
    assert strict_inputs.dataset == primary_inputs.dataset == dataset()
    assert strict_inputs.configuration != primary_inputs.configuration
    assert strict_inputs.ledger_key == "series-strict"
    # The ETH run shares the default configuration but not the dataset.
    assert eth_inputs.configuration == primary_inputs.configuration
    assert eth_inputs.dataset == dataset(symbol="ETHUSDT") != primary_inputs.dataset
    assert len({primary_inputs.ledger_key, strict_inputs.ledger_key, eth_inputs.ledger_key}) == 3


def test_repeated_materialization_is_deterministic(stores) -> None:
    declaration = stores.declare()

    first = stores.materialize()
    second = stores.materialize()
    third = stores.materialize()

    assert first == second == third
    assert first.binding == second.binding == declaration
    assert dataset_bytes(first.dataset) == dataset_bytes(second.dataset)
    assert configuration_bytes(first.configuration) == configuration_bytes(second.configuration)
    assert (
        phase27_dataset_digest(first.dataset)
        == phase27_dataset_digest(third.dataset)
        == declaration.dataset_digest
    )
    assert (
        phase28_configuration_digest(first.configuration)
        == phase28_configuration_digest(third.configuration)
        == declaration.configuration_digest
    )


def test_restart_equivalent_materialization_is_deterministic(tmp_path) -> None:
    """Fresh store instances over the same roots yield the identical verified value."""

    before_restart = Stores("file", tmp_path)
    declaration = before_restart.declare()
    first = before_restart.materialize()

    after_restart = before_restart.reopened()
    second = after_restart.materialize()
    third = after_restart.reopened().materialize()

    assert first == second == third
    assert second.binding == declaration
    assert second.dataset == dataset() and second.configuration == PIPELINE
    assert second.ledger_key == LEDGER_KEY
    assert phase27_dataset_digest(second.dataset) == declaration.dataset_digest
    assert phase28_configuration_digest(second.configuration) == declaration.configuration_digest


def test_materialization_is_deterministic_across_independent_roots(tmp_path) -> None:
    root_a = Stores("file", tmp_path / "root-a")
    root_b = Stores("file", tmp_path / "root-b")
    memory = Stores("memory", tmp_path / "memory")
    for candidate in (root_a, root_b, memory):
        candidate.declare()

    assert root_a.materialize() == root_b.materialize() == memory.materialize()


def test_verified_inputs_cannot_be_constructed_around_a_mismatched_dataset() -> None:
    """Construction is the verification: no unverified value of this type can exist."""

    declaration = binding_for()
    with pytest.raises(AnalysisInputError, match="dataset_digest"):
        VerifiedRunInputs(
            binding=declaration, dataset=dataset(symbol="ETHUSDT"), configuration=PIPELINE
        )


def test_verified_inputs_cannot_be_constructed_around_a_mismatched_configuration() -> None:
    declaration = binding_for()
    with pytest.raises(AnalysisInputError, match="configuration_digest"):
        VerifiedRunInputs(
            binding=declaration,
            dataset=dataset(),
            configuration=configuration(threshold=15, horizon=4),
        )


def test_verified_inputs_require_the_frozen_value_types() -> None:
    declaration = binding_for()
    with pytest.raises(AnalysisInputError, match="DeclaredRunBinding"):
        VerifiedRunInputs(
            binding={"not": "a binding"},  # type: ignore[arg-type]
            dataset=dataset(),
            configuration=PIPELINE,
        )
    with pytest.raises(AnalysisInputError, match="ReplayDataset"):
        VerifiedRunInputs(
            binding=declaration,
            dataset=dataset_bytes(dataset()),  # type: ignore[arg-type]
            configuration=PIPELINE,
        )
    with pytest.raises(AnalysisInputError, match="BacktestConfiguration"):
        VerifiedRunInputs(
            binding=declaration,
            dataset=dataset(),
            configuration=configuration_bytes(PIPELINE),  # type: ignore[arg-type]
        )


def test_directly_constructed_verified_inputs_equal_the_materialized_ones(stores) -> None:
    declaration = stores.declare()
    assert stores.materialize() == VerifiedRunInputs(
        binding=declaration, dataset=dataset(), configuration=PIPELINE
    )


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_caller_composes_the_verified_inputs_into_the_identical_26h_run(
    tmp_path, label, prices
) -> None:
    """Phase 30 ends at the verified inputs; the caller invokes Phase 26H.

    The materialized dataset, configuration, and ledger key drive exactly the
    run an uninterrupted caller would produce, before and after a restart.
    """

    history = dataset(prices=prices)
    declared = Stores("file", tmp_path)
    declaration = declared.declare(history, PIPELINE)

    inputs = declared.materialize()
    first_run = run_declared_history(
        inputs.dataset,
        inputs.configuration,
        FileLedgerStore(tmp_path / "ledgers"),
        inputs.ledger_key,
    )
    first_run.persist()
    assert first_run.recovered_from is None

    restarted_inputs = declared.reopened().materialize()
    assert restarted_inputs == inputs
    assert restarted_inputs.binding == declaration
    restarted = run_declared_history(
        restarted_inputs.dataset,
        restarted_inputs.configuration,
        FileLedgerStore(tmp_path / "ledgers"),
        restarted_inputs.ledger_key,
    )
    assert_ledgers_equal(restarted.lifecycle, first_run.lifecycle)
    assert restarted.recovered_from is not None
    assert restarted.recovered_from == first_run.persist()
    assert_ledgers_equal(restarted.lifecycle, uninterrupted(prices))
