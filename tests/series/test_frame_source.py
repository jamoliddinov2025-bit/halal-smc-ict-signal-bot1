"""Phase 26F: the sanctioned frame-regeneration seam over the frozen replay.

Every frame here is the actual Phase 17 publication frame produced by the
frozen Phase 3-17 chain inside the frozen Phase 20 replay machinery — the
source is proven frame-for-frame equal to ``replay_history`` signals, and the
defining integration restarts the whole analytics arc (26B -> 26C -> 26D ->
fresh 26F regeneration -> 26E recovery -> continuation) on production seams
only.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.backtest import replay_history
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus
from smcsignal.analytics import (
    AnalyticsObserver,
    SignalOutcomeLifecycle,
    ledger_bytes,
    recover_lifecycle,
    snapshot_ledger,
)
from smcsignal.persistence import FileLedgerStore
from smcsignal.series import SeriesFrameSource, series_frames
from tests.backtest.helpers import PRIMARY_PRICES, configuration, dataset
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL

SCENARIOS = (
    ("rising", PRIMARY_PRICES),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)


def dataset_for(prices: tuple) -> object:
    return dataset(prices=prices)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_frame_source_drain_equals_phase20_replay_signals_exactly(label, prices) -> None:
    ds = dataset_for(prices)
    cfg = configuration()
    source = SeriesFrameSource(ds, cfg)
    assert source.processed_count == 0
    assert source.complete is False
    assert source.dataset is ds
    assert source.configuration is cfg

    drained = []
    while not source.complete:
        drained.append(source.update())
    assert source.complete is True

    expected = tuple(step.signal for step in replay_history(ds, cfg).steps)
    assert tuple(drained) == expected


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_frame_count_equals_candle_count_and_frames_are_real_publications(label, prices) -> None:
    ds = dataset_for(prices)
    frames = series_frames(ds, configuration())
    assert len(frames) == len(ds.candles)
    assert all(isinstance(frame, SignalSnapshot) for frame in frames)


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_buy_publications_match_the_frozen_replay_exactly(label, prices) -> None:
    ds = dataset_for(prices)
    cfg = configuration()
    expected_buys = tuple(
        step.index
        for step in replay_history(ds, cfg).steps
        if step.signal.status is SignalStatus.BUY_SIGNAL
    )
    source_buys = tuple(
        index
        for index, frame in enumerate(series_frames(ds, cfg))
        if frame.status is SignalStatus.BUY_SIGNAL
    )
    assert source_buys == expected_buys
    assert expected_buys  # every audited fixture publishes real BUY facts


def test_prefix_stability_for_the_complete_history() -> None:
    cfg = configuration()
    for prices in (PRIMARY_PRICES, RISE_THEN_FALL):
        full = series_frames(dataset_for(prices), cfg)
        for split in (1, len(prices) // 3, len(prices) // 2, len(prices) - 1, len(prices)):
            prefix = series_frames(dataset_for(prices[:split]), cfg)
            assert prefix == full[:split], (len(prices), split)


def test_independent_sources_agree_exactly() -> None:
    ds = dataset_for(PRIMARY_PRICES)
    cfg = configuration()
    first = series_frames(ds, cfg)
    second = series_frames(ds, cfg)
    third = SeriesFrameSource(ds, cfg).frames()
    assert first == second == third
    assert series_frames(dataset_for(RISE_THEN_FALL), cfg) == series_frames(
        dataset_for(RISE_THEN_FALL), cfg
    )


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_streaming_update_matches_batch_and_frames_drain(label, prices) -> None:
    ds = dataset_for(prices)
    cfg = configuration()
    expected = series_frames(ds, cfg)

    source = SeriesFrameSource(ds, cfg)
    streamed = []
    while not source.complete:
        streamed.append(source.update())
        assert source.processed_count == len(streamed)
    assert tuple(streamed) == expected

    partial = SeriesFrameSource(ds, cfg)
    cutoff = min(2, len(ds.candles))
    for _ in range(cutoff):
        partial.update()
    assert partial.frames() == expected[cutoff:]
    assert partial.complete is True


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_exhaustion_fails_deterministically(label, prices) -> None:
    source = SeriesFrameSource(dataset_for(prices), configuration())
    source.frames()
    assert source.complete is True
    with pytest.raises(AnalysisInputError, match="exhausted"):
        source.update()
    with pytest.raises(AnalysisInputError, match="exhausted"):
        source.update()  # exhaustion is stable, not one-shot


def test_input_validation_is_inherited_not_reimplemented() -> None:
    cfg = configuration()
    ds = dataset_for(PRIMARY_PRICES)
    with pytest.raises(AnalysisInputError, match="ReplayDataset"):
        SeriesFrameSource(PRIMARY_PRICES, cfg)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="BacktestConfiguration"):
        SeriesFrameSource(ds, cfg.signal_eligibility)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match="higher_candles must supply exactly"):
        SeriesFrameSource(dataset(higher={}), cfg)


def test_the_source_exposes_no_ledger_or_lifecycle_surface() -> None:
    source = SeriesFrameSource(dataset_for(PRIMARY_PRICES), configuration())
    for forbidden in (
        "observer",
        "tracker",
        "lifecycle",
        "ledger",
        "snapshot",
        "store",
        "recover",
        "deliver",
        "outcome",
    ):
        assert not hasattr(source, forbidden), f"frame source must not expose {forbidden}"


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


def test_full_arc_restart_equivalence_on_production_seams(tmp_path) -> None:
    """The defining integration: 3-17 -> 26F -> 26B -> 26C -> 26D -> 26F -> 26E."""

    ds = dataset_for(PRIMARY_PRICES)
    cfg = configuration()
    frames = series_frames(ds, cfg)
    settings = cfg.outcome_tracking

    # Uninterrupted run over the regenerated real publication frames.
    full = SignalOutcomeLifecycle(AnalyticsObserver(settings))
    for frame in frames:
        full.update(frame)
    assert full.observer.observations
    assert full.finalized_outcomes  # real market finalization exists

    # The run before the restart, persisted through the Phase 26D store.
    split = len(frames) // 2
    partial = SignalOutcomeLifecycle(AnalyticsObserver(settings))
    for frame in frames[:split]:
        partial.update(frame)
    store = FileLedgerStore(tmp_path / "ledgers")
    store.save("series-primary", snapshot_ledger(partial))

    # The restart: load the stored snapshot, regenerate history through a fresh
    # Phase 26F source (never tests/analytics helpers), recover, and continue.
    expected = store.load("series-primary")
    assert expected is not None
    regenerated = series_frames(ds, cfg)
    assert regenerated == frames
    recovered = recover_lifecycle(regenerated[:split], expected)
    for frame in regenerated[split:]:
        recovered.lifecycle.update(frame)

    assert_ledgers_equal(recovered.lifecycle, full)
    assert recovered.frames_verified == split
    assert recovered.verified_snapshot.snapshot_id == snapshot_ledger(partial).snapshot_id
