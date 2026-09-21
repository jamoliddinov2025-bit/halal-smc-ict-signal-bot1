"""Phase 31 composition: a verified run executes through the frozen Phase 26H seam.

A ``VerifiedRunInputs`` materialized through the real Phase 27/28/29 stores
executes through ``execute_verified_run`` into exactly the ``LedgerSession``
the frozen Phase 26H seam produces for the same dataset, configuration,
store, and declared ledger key — equal to the manual composition, equal to
the uninterrupted Phase 26B lifecycle, restart-safe, and deterministic across
repetitions, independent stores, and restart-equivalent re-materialization.
"""

from __future__ import annotations

import pytest

from smcsignal.analytics import ledger_bytes, snapshot_ledger
from smcsignal.composition import execute_verified_run
from smcsignal.materialization import VerifiedRunInputs
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from smcsignal.runs import run_declared_history
from smcsignal.sessions import LedgerSession
from tests.backtest.helpers import configuration, dataset
from tests.declarations.test_run_binding import (
    LEDGER_KEY,
    PIPELINE,
    binding_for,
)
from tests.materialization.test_declared_inputs import Stores
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL
from tests.runs.test_series_run import assert_ledgers_equal, uninterrupted

SCENARIOS = (
    ("rising", tuple(range(20, 37))),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)


def verified_inputs(history=None, pipeline=None, *, ledger_key: str = LEDGER_KEY):
    """Directly constructed verified inputs: construction is the Phase 30 verification."""

    history = dataset() if history is None else history
    pipeline = PIPELINE if pipeline is None else pipeline
    return VerifiedRunInputs(
        binding=binding_for(history, pipeline, ledger_key=ledger_key),
        dataset=history,
        configuration=pipeline,
    )


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileLedgerStore(tmp_path / "ledgers")
    return MemoryLedgerStore()


@pytest.fixture(params=["file", "memory"])
def stores(request, tmp_path) -> Stores:
    return Stores(request.param, tmp_path)


# --------------------------------------------------------------------------
# A valid verified value executes into the Phase 26H session
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_verified_inputs_execute_into_a_ledger_session(store, label, prices) -> None:
    inputs = verified_inputs(dataset(prices=prices))

    session = execute_verified_run(inputs, store)

    assert isinstance(session, LedgerSession)
    assert session.store is store
    assert session.key == inputs.ledger_key == LEDGER_KEY
    assert session.config == inputs.configuration.outcome_tracking
    assert session.recovered_from is None  # first run is always a fresh bootstrap
    assert session.frames_verified == len(inputs.dataset.candles)
    assert store.load(inputs.ledger_key) == snapshot_ledger(session.lifecycle)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_execution_equals_the_direct_phase26h_composition_exactly(store, label, prices) -> None:
    inputs = verified_inputs(dataset(prices=prices))
    manual_store = MemoryLedgerStore()

    adapted = execute_verified_run(inputs, store)
    manual = run_declared_history(
        inputs.dataset, inputs.configuration, manual_store, inputs.ledger_key
    )

    assert_ledgers_equal(adapted.lifecycle, manual.lifecycle)
    assert adapted.frames_verified == manual.frames_verified
    assert adapted.recovered_from == manual.recovered_from
    assert adapted.config == manual.config
    assert adapted.key == manual.key
    assert store.load(inputs.ledger_key) == manual_store.load(inputs.ledger_key)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_execution_equals_the_uninterrupted_lifecycle(store, label, prices) -> None:
    session = execute_verified_run(verified_inputs(dataset(prices=prices)), store)
    full = uninterrupted(prices)

    assert_ledgers_equal(session.lifecycle, full)
    assert snapshot_ledger(session.lifecycle).snapshot_id == snapshot_ledger(full).snapshot_id
    assert ledger_bytes(snapshot_ledger(session.lifecycle)) == ledger_bytes(snapshot_ledger(full))


def test_materialized_inputs_execute_like_directly_constructed_ones(stores, store) -> None:
    """The Phase 30 output, straight from the real stores, is what the adapter consumes."""

    declaration = stores.declare()
    inputs = stores.materialize()
    assert inputs == verified_inputs()
    assert inputs.binding == declaration

    session = execute_verified_run(inputs, store)

    assert session.key == declaration.ledger_key
    assert_ledgers_equal(session.lifecycle, uninterrupted(tuple(range(20, 37))))
    assert store.load(declaration.ledger_key) == snapshot_ledger(session.lifecycle)


# --------------------------------------------------------------------------
# The declared inputs — not defaults — drive the run
# --------------------------------------------------------------------------


def test_the_declared_configuration_drives_both_frames_and_outcomes(store) -> None:
    default_run = execute_verified_run(verified_inputs(), store)
    assert default_run.config == PIPELINE.outcome_tracking
    assert default_run.lifecycle.observer.config == PIPELINE.outcome_tracking

    strict = configuration(threshold=15, horizon=4)
    strict_run = execute_verified_run(
        verified_inputs(pipeline=strict, ledger_key="series-strict"), MemoryLedgerStore()
    )
    assert strict_run.config == strict.outcome_tracking
    assert (
        snapshot_ledger(strict_run.lifecycle).snapshot_id
        != snapshot_ledger(default_run.lifecycle).snapshot_id
    )


def test_the_declared_ledger_key_addresses_the_store(store) -> None:
    primary = execute_verified_run(verified_inputs(ledger_key="series-primary"), store)
    alternate = execute_verified_run(verified_inputs(ledger_key="series-alt"), store)

    assert primary.key == "series-primary" and alternate.key == "series-alt"
    assert store.contains("series-primary") and store.contains("series-alt")
    assert not store.contains("series-never-declared")
    assert store.load("series-primary") == snapshot_ledger(primary.lifecycle)
    assert store.load("series-alt") == snapshot_ledger(alternate.lifecycle)


def test_the_declared_dataset_drives_the_run(store) -> None:
    eth = dataset(symbol="ETHUSDT")
    eth_run = execute_verified_run(verified_inputs(eth, ledger_key="series-eth"), store)
    btc_run = execute_verified_run(verified_inputs(ledger_key="series-btc"), store)

    assert eth_run.lifecycle.observer.observations != btc_run.lifecycle.observer.observations
    assert_ledgers_equal(
        eth_run.lifecycle,
        run_declared_history(eth, PIPELINE, MemoryLedgerStore(), "series-eth").lifecycle,
    )


# --------------------------------------------------------------------------
# Determinism and restart safety, seam-only
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_repeated_execution_recovers_through_the_seam(store, label, prices) -> None:
    inputs = verified_inputs(dataset(prices=prices))

    first = execute_verified_run(inputs, store)
    second = execute_verified_run(inputs, store)
    third = execute_verified_run(inputs, store)

    assert first.recovered_from is None
    assert second.recovered_from == snapshot_ledger(first.lifecycle)
    assert third.recovered_from == snapshot_ledger(second.lifecycle)
    assert_ledgers_equal(first.lifecycle, second.lifecycle)
    assert_ledgers_equal(second.lifecycle, third.lifecycle)
    assert first.frames_verified == second.frames_verified == third.frames_verified


def test_repeated_equivalent_calls_are_deterministic_across_independent_stores() -> None:
    inputs = verified_inputs()

    runs = [execute_verified_run(inputs, MemoryLedgerStore()) for _ in range(3)]

    for candidate in runs[1:]:
        assert_ledgers_equal(runs[0].lifecycle, candidate.lifecycle)
        assert candidate.frames_verified == runs[0].frames_verified
        assert candidate.recovered_from is None
    snapshots = {snapshot_ledger(run.lifecycle).snapshot_id for run in runs}
    assert len(snapshots) == 1


def test_equal_verified_values_execute_identically(tmp_path) -> None:
    """Two equal ``VerifiedRunInputs`` (constructed vs materialized) are interchangeable."""

    stores = Stores("file", tmp_path)
    stores.declare()
    materialized = stores.materialize()
    constructed = verified_inputs()
    assert materialized == constructed and materialized is not constructed

    from_materialized = execute_verified_run(materialized, MemoryLedgerStore())
    from_constructed = execute_verified_run(constructed, MemoryLedgerStore())

    assert_ledgers_equal(from_materialized.lifecycle, from_constructed.lifecycle)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_restart_equivalent_materialization_recovers_the_same_run(tmp_path, label, prices) -> None:
    """Materialize → execute → restart → re-materialize → execute recovers exactly."""

    declared = Stores("file", tmp_path)
    declared.declare(dataset(prices=prices), PIPELINE)
    ledgers = tmp_path / "ledgers"

    first = execute_verified_run(declared.materialize(), FileLedgerStore(ledgers))
    assert first.recovered_from is None

    restarted_inputs = declared.reopened().materialize()
    restarted = execute_verified_run(restarted_inputs, FileLedgerStore(ledgers))

    assert restarted.recovered_from == snapshot_ledger(first.lifecycle)
    assert_ledgers_equal(restarted.lifecycle, first.lifecycle)
    assert_ledgers_equal(restarted.lifecycle, uninterrupted(prices))


@pytest.mark.parametrize("split_fraction", (3, 2))
def test_extended_history_continues_the_persisted_run_through_the_seam(
    store, split_fraction
) -> None:
    prices = tuple(range(20, 37))
    split = len(prices) // split_fraction

    opened = execute_verified_run(verified_inputs(dataset(prices=prices[:split])), store)
    assert opened.recovered_from is None

    resumed = execute_verified_run(verified_inputs(dataset(prices=prices)), store)

    assert resumed.recovered_from == snapshot_ledger(opened.lifecycle)
    assert_ledgers_equal(resumed.lifecycle, uninterrupted(prices))
