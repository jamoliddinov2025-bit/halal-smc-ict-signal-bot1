"""Phase 26H: the deterministic offline composition seam.

One declared pipeline configuration must drive BOTH frame generation (26F)
and outcome evaluation (26G): ``run_declared_history`` is proven equal to the
manual composition, equal to the uninterrupted Phase 26B lifecycle, and
restart-safe through the seam alone — across the audited datasets.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analytics import (
    AnalyticsObserver,
    SignalOutcomeLifecycle,
    ledger_bytes,
    snapshot_ledger,
)
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from smcsignal.runs import run_declared_history
from smcsignal.series import series_frames
from smcsignal.sessions import open_ledger_session
from tests.backtest.helpers import PRIMARY_PRICES, configuration, dataset
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL

KEY = "series-primary"
PIPELINE = configuration()

SCENARIOS = (
    ("rising", PRIMARY_PRICES),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)


def uninterrupted(prices: tuple) -> SignalOutcomeLifecycle:
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(PIPELINE.outcome_tracking))
    for frame in series_frames(dataset(prices=prices), PIPELINE):
        lifecycle.update(frame)
    return lifecycle


def assert_ledgers_equal(left: SignalOutcomeLifecycle, right: SignalOutcomeLifecycle) -> None:
    assert left.observer.observations == right.observer.observations
    assert left.open_outcomes == right.open_outcomes
    assert left.finalized_outcomes == right.finalized_outcomes
    assert left.strategy_stats == right.strategy_stats
    assert left.monthly_report() == right.monthly_report()
    left_snapshot = snapshot_ledger(left)
    right_snapshot = snapshot_ledger(right)
    assert left_snapshot == right_snapshot
    assert left_snapshot.snapshot_id == right_snapshot.snapshot_id
    assert ledger_bytes(left_snapshot) == ledger_bytes(right_snapshot)


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileLedgerStore(tmp_path / "ledgers")
    return MemoryLedgerStore()


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_run_equals_the_manual_composition_exactly(store, label, prices) -> None:
    ds = dataset(prices=prices)
    manual_store = MemoryLedgerStore()

    ran = run_declared_history(ds, PIPELINE, store, KEY)
    manual = open_ledger_session(
        manual_store, KEY, PIPELINE.outcome_tracking, series_frames(ds, PIPELINE)
    )

    assert_ledgers_equal(ran.lifecycle, manual.lifecycle)
    assert ran.frames_verified == manual.frames_verified
    assert ran.recovered_from == manual.recovered_from
    assert ran.config == manual.config
    assert store.load(KEY) == manual_store.load(KEY)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_run_equals_the_uninterrupted_lifecycle(store, label, prices) -> None:
    ran = run_declared_history(dataset(prices=prices), PIPELINE, store, KEY)
    full = uninterrupted(prices)

    assert_ledgers_equal(ran.lifecycle, full)
    # Identity and canonical bytes are part of the same equality, pinned
    # explicitly because restart safety depends on them.
    assert snapshot_ledger(ran.lifecycle).snapshot_id == snapshot_ledger(full).snapshot_id
    assert ledger_bytes(snapshot_ledger(ran.lifecycle)) == ledger_bytes(snapshot_ledger(full))
    assert ran.lifecycle.strategy_stats == full.strategy_stats
    assert ran.lifecycle.monthly_report() == full.monthly_report()


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_fresh_run_provenance_and_open_time_durability(store, label, prices) -> None:
    ds = dataset(prices=prices)
    ran = run_declared_history(ds, PIPELINE, store, KEY)

    assert ran.recovered_from is None  # first run is always a fresh bootstrap
    assert ran.frames_verified == len(ds.candles)
    assert ran.key == KEY
    assert store.load(KEY) == snapshot_ledger(ran.lifecycle)


def test_one_configuration_supplies_both_frame_generation_and_outcome_tracking(store) -> None:
    ds = dataset(prices=PRIMARY_PRICES)
    ran = run_declared_history(ds, PIPELINE, store, KEY)

    # The session runs under exactly the declared configuration's outcome
    # tracking — the same object that parameterized frame generation.
    assert ran.config == PIPELINE.outcome_tracking
    assert ran.lifecycle.observer.config == PIPELINE.outcome_tracking

    # Counterfactual: a different declared horizon changes the evaluation,
    # proving the supplied configuration — not any default — drives outcomes.
    other = configuration(horizon=5)
    ran_other = run_declared_history(ds, other, MemoryLedgerStore(), KEY)
    assert ran_other.config == other.outcome_tracking
    assert (
        snapshot_ledger(ran_other.lifecycle).snapshot_id
        != snapshot_ledger(ran.lifecycle).snapshot_id
    )


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_rerun_recovers_through_the_seam(store, label, prices) -> None:
    ds = dataset(prices=prices)
    first = run_declared_history(ds, PIPELINE, store, KEY)
    assert first.recovered_from is None

    second = run_declared_history(ds, PIPELINE, store, KEY)
    assert second.recovered_from is not None
    assert second.recovered_from == snapshot_ledger(first.lifecycle)
    assert_ledgers_equal(second.lifecycle, first.lifecycle)
    assert second.frames_verified == len(ds.candles)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
@pytest.mark.parametrize("split_fraction", (3, 2))
def test_restart_over_extended_history_through_the_seam(
    store, label, prices, split_fraction
) -> None:
    """Persist -> restart -> verified recovery -> continuation, seam-only."""

    split = len(prices) // split_fraction
    open_run = run_declared_history(dataset(prices=prices[:split]), PIPELINE, store, KEY)
    assert open_run.recovered_from is None

    resumed = run_declared_history(dataset(prices=prices), PIPELINE, store, KEY)
    assert resumed.recovered_from is not None
    assert resumed.recovered_from == snapshot_ledger(open_run.lifecycle)
    assert_ledgers_equal(resumed.lifecycle, uninterrupted(prices))


def test_restart_across_distinct_store_instances_through_the_seam(tmp_path) -> None:
    root = tmp_path / "ledgers"
    prices = PRIMARY_PRICES
    split = len(prices) // 2

    run_declared_history(dataset(prices=prices[:split]), PIPELINE, FileLedgerStore(root), KEY)
    resumed = run_declared_history(dataset(prices=prices), PIPELINE, FileLedgerStore(root), KEY)

    assert resumed.recovered_from is not None
    assert_ledgers_equal(resumed.lifecycle, uninterrupted(prices))


def test_different_series_under_the_same_key_fails_with_store_unchanged(store) -> None:
    run_declared_history(dataset(prices=PRIMARY_PRICES), PIPELINE, store, KEY)
    before_bytes = ledger_bytes(store.load(KEY))

    # Same candles, same configuration, different series identity: counts can
    # trigger the candidate comparison inside 26G, but complete snapshot
    # equality alone authorizes — the run fails and the store is untouched.
    foreign = dataset(symbol="ETHUSDT")
    with pytest.raises(AnalysisInputError):
        run_declared_history(foreign, PIPELINE, store, KEY)
    assert ledger_bytes(store.load(KEY)) == before_bytes

    rerun = run_declared_history(dataset(prices=PRIMARY_PRICES), PIPELINE, store, KEY)
    assert_ledgers_equal(rerun.lifecycle, uninterrupted(PRIMARY_PRICES))


def test_run_is_deterministic_across_independent_stores() -> None:
    ds = dataset(prices=PRIMARY_PRICES)
    first = run_declared_history(ds, PIPELINE, MemoryLedgerStore(), KEY)
    second = run_declared_history(ds, PIPELINE, MemoryLedgerStore(), KEY)
    assert_ledgers_equal(first.lifecycle, second.lifecycle)
    assert first.frames_verified == second.frames_verified
