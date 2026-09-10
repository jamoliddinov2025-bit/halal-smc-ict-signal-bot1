"""Phase 26C round-trip: real lifecycle ledgers survive snapshot/restore exactly.

Every ledger here is built by the real Phase 26B lifecycle over the real Phase
3-17 chain (never handcrafted models alone): snapshot -> canonical bytes ->
restore -> read-only ledger must reproduce observations, OPEN outcomes,
finalized outcomes, configuration, StrategyStats, MonthlyReport, canonical
bytes, and the content-addressed snapshot identity.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.outcome_tracking import OutcomeTrackingConfig
from smcsignal.analytics import (
    AnalyticsObserver,
    RestoredAnalyticsLedger,
    SignalOutcomeLifecycle,
    ledger_bytes,
    load_ledger_bytes,
    run_lifecycle,
    snapshot_ledger,
)
from tests.analytics.helpers import eligibility_chain, publish, real_engine
from tests.outcome_tracking.helpers import FALLING_TAIL, FLAT_TAIL, candles_for

# (label, candles, horizon): empty, WIN, LOSS, BREAKEVEN, mixed finals, open-only.
SCENARIOS = (
    ("empty", None, None),
    ("win", None, None),
    ("loss", FALLING_TAIL, None),
    ("breakeven", FLAT_TAIL, None),
    ("mixed", FLAT_TAIL, 3),
    ("open_only", None, 13),
)


def frames_for(candles=None):
    eligible = eligibility_chain(candles_for(candles) if candles is not None else None)
    return publish(real_engine(eligible), eligible)


def lifecycle_for(label, candles=None, horizon=None):
    config = (
        OutcomeTrackingConfig() if horizon is None else OutcomeTrackingConfig(horizon_bars=horizon)
    )
    lifecycle = SignalOutcomeLifecycle(AnalyticsObserver(config))
    if label != "empty":
        run_lifecycle(frames_for(candles), lifecycle)
    return lifecycle


@pytest.mark.parametrize(("label", "candles", "horizon"), SCENARIOS)
def test_real_lifecycle_ledger_round_trips_exactly(label, candles, horizon) -> None:
    lifecycle = lifecycle_for(label, candles, horizon)
    snapshot = snapshot_ledger(lifecycle)

    blob = ledger_bytes(snapshot)
    restored_snapshot = load_ledger_bytes(blob)
    restored = RestoredAnalyticsLedger(restored_snapshot)

    observer = lifecycle.observer
    assert restored_snapshot == snapshot
    assert restored_snapshot.snapshot_id == snapshot.snapshot_id
    assert ledger_bytes(restored_snapshot) == blob
    assert restored.observations == observer.observations
    assert restored.open_outcomes == observer.open_outcomes
    assert restored.finalized_outcomes == observer.finalized_outcomes
    assert restored.settings == observer.config
    assert restored.configuration_hash == observer.configuration_hash


@pytest.mark.parametrize(("label", "candles", "horizon"), SCENARIOS)
def test_restored_ledger_reproduces_stats_and_monthly_report(label, candles, horizon) -> None:
    lifecycle = lifecycle_for(label, candles, horizon)
    snapshot = snapshot_ledger(lifecycle)
    restored = RestoredAnalyticsLedger(load_ledger_bytes(ledger_bytes(snapshot)))

    observer = lifecycle.observer
    assert restored.strategy_stats == observer.strategy_stats
    assert restored.monthly_report() == observer.monthly_report()


def test_snapshot_and_restore_are_deterministic_across_independent_runs() -> None:
    first = snapshot_ledger(lifecycle_for("mixed", FLAT_TAIL, 3))
    second = snapshot_ledger(lifecycle_for("mixed", FLAT_TAIL, 3))
    assert first.snapshot_id == second.snapshot_id
    assert ledger_bytes(first) == ledger_bytes(second)
    assert load_ledger_bytes(ledger_bytes(first)) == load_ledger_bytes(ledger_bytes(second))


def test_changed_contents_change_the_identity() -> None:
    full = snapshot_ledger(lifecycle_for("win", None, None))
    partial = snapshot_ledger(lifecycle_for("loss", FALLING_TAIL, None))
    assert full.snapshot_id != partial.snapshot_id
    assert ledger_bytes(full) != ledger_bytes(partial)

    different_horizon = snapshot_ledger(lifecycle_for("win", None, 8))
    assert different_horizon.snapshot_id != full.snapshot_id


def test_snapshot_accepts_observer_and_lifecycle_sources_only() -> None:
    from smcsignal.analysis.errors import AnalysisInputError

    frames = frames_for(None)
    lifecycle = lifecycle_for("win", None, None)
    assert snapshot_ledger(lifecycle.observer) == snapshot_ledger(lifecycle)
    with pytest.raises(AnalysisInputError, match="AnalyticsObserver or a SignalOutcomeLifecycle"):
        snapshot_ledger(frames[0])  # type: ignore[arg-type]


def test_restored_ledger_is_read_only_historical_fact() -> None:
    restored = RestoredAnalyticsLedger(snapshot_ledger(lifecycle_for("mixed", FLAT_TAIL, 3)))
    for forbidden in ("observe", "observe_all", "record_finalized", "update"):
        assert not hasattr(restored, forbidden), f"restored ledger must not expose {forbidden}"
    from smcsignal.analysis.errors import AnalysisInputError

    with pytest.raises(AnalysisInputError, match="requires a LedgerSnapshot"):
        RestoredAnalyticsLedger(restored.observations[0])  # type: ignore[arg-type]
    assert isinstance(restored.snapshot.snapshot_id, str)
