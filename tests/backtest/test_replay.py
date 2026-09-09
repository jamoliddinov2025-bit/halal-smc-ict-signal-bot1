"""Replay ordering, determinism, chunking, prefix, and dataset behavior tests."""

from __future__ import annotations

import pytest

from smcsignal.analysis.backtest import (
    HistoricalReplay,
    replay_history,
    run_backtest,
)
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import OutcomeStatus
from tests.backtest.helpers import (
    PRIMARY_PRICES,
    bars,
    buy_frames,
    configuration,
    dataset,
    report,
)


def test_replay_processes_every_candle_in_order() -> None:
    engine = HistoricalReplay(dataset(), configuration())
    steps = [engine.update() for _ in range(len(engine.dataset.candles))]
    assert [step.index for step in steps] == list(range(17))
    for index, step in enumerate(steps):
        observation = step.signal.candidate.signal.candle
        assert observation.candle_index == index
    assert engine.complete
    assert engine.processed_count == 17


def test_update_rejects_calls_beyond_the_declared_history() -> None:
    engine = HistoricalReplay(dataset(), configuration())
    while not engine.complete:
        engine.update()
    with pytest.raises(AnalysisInputError, match=r"declared history is exhausted"):
        engine.update()


def test_result_requires_the_whole_history() -> None:
    engine = HistoricalReplay(dataset(), configuration())
    engine.update()
    with pytest.raises(AnalysisInputError, match=r"whole declared history"):
        engine.result()


def test_batch_replay_equals_streaming_replay() -> None:
    config = configuration()
    batch = replay_history(dataset(), config)
    engine = HistoricalReplay(dataset(), config)
    while not engine.complete:
        engine.update()
    assert engine.result() == batch


def test_replay_is_independent_of_chunking() -> None:
    config = configuration()
    expected = replay_history(dataset(), config)
    for sizes in ([1] * 17, [3, 5, 2, 7], [16, 1], [8, 9]):
        engine = HistoricalReplay(dataset(), config)
        for size in sizes:
            for _ in range(size):
                engine.update()
        assert engine.result() == expected


def test_replay_twice_produces_identical_results() -> None:
    config = configuration()
    first = replay_history(dataset(), config)
    second = replay_history(dataset(), config)
    assert first == second
    assert first.replay_id == second.replay_id


def test_engine_steps_are_read_only_snapshots() -> None:
    engine = HistoricalReplay(dataset(), configuration())
    while not engine.complete:
        engine.update()
    steps = engine.steps
    assert steps is not engine.steps  # a fresh tuple every read
    assert steps == engine.steps


def test_prefix_replay_matches_the_full_replay_prefix() -> None:
    config = configuration()
    full = HistoricalReplay(dataset(), config)
    while not full.complete:
        full.update()
    for cut in (1, 5, 10, 16):
        partial = HistoricalReplay(dataset(), config)
        for _ in range(cut):
            partial.update()
        assert partial.steps == full.steps[:cut]
    prefix_dataset = dataset(prices=PRIMARY_PRICES[:12])
    prefix = replay_history(prefix_dataset, config)
    assert prefix.steps == full.steps[:12]


def test_prefix_outcome_frames_equal_full_outcome_frames() -> None:
    config = configuration()
    full = replay_history(dataset(), config)
    prefix = replay_history(dataset(prices=PRIMARY_PRICES[:12]), config)
    assert prefix.outcomes == full.outcomes[:12]
    assert prefix.signal_frames == full.signal_frames[:12]
    assert prefix.attributions == full.attributions[:12]


def test_zero_signal_dataset_is_valid_and_deterministic() -> None:
    config = configuration()
    empty_history = dataset(prices=(20, 21))
    first = run_backtest([empty_history], config)
    second = run_backtest([empty_history], config)
    assert first.signals == ()
    assert first.performance.overall.total_buy_signals == 0
    assert first.performance.overall.win_rate is None
    assert first == second
    assert first.backtest_id == second.backtest_id


def test_dataset_without_higher_histories_is_valid() -> None:
    config = configuration()
    lonely = dataset(higher={"1h": (), "4h": ()})
    result = run_backtest([lonely], config)
    assert result.signals == ()  # no completed HTF context means no mtf_bullish label
    assert result.performance.overall.total_buy_signals == 0


def test_engine_requires_exactly_the_configured_higher_timeframes() -> None:
    config = configuration()
    mismatched = dataset(higher={"1h": bars("1h", (15, 10, 16))})
    with pytest.raises(AnalysisInputError, match=r"exactly the configured higher timeframes"):
        HistoricalReplay(mismatched, config)


def test_primaries_below_the_configured_higher_timeframes_are_rejected() -> None:
    from smcsignal.analysis.errors import AnalysisConfigurationError

    # The dataset contract itself refuses histories whose HTFs are not strict
    # multiples, so the replay can never draw an inconsistent MTF join.
    with pytest.raises(AnalysisConfigurationError, match=r"not a strictly higher integer multiple"):
        dataset(timeframe="8h")


def test_run_backtest_accepts_a_dataset_iterator() -> None:
    config = configuration()
    datasets = iter([dataset(), dataset(symbol="ETHUSDT")])
    result = run_backtest(datasets, config)
    assert result.performance.overall.total_buy_signals == 8


def test_engine_rejects_wrong_input_types() -> None:
    config = configuration()
    with pytest.raises(AnalysisInputError, match=r"requires a ReplayDataset"):
        HistoricalReplay("dataset", config)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError, match=r"requires a BacktestConfiguration"):
        HistoricalReplay(dataset(), None)  # type: ignore[arg-type]


def test_run_backtest_rejects_empty_and_malformed_datasets() -> None:
    config = configuration()
    with pytest.raises(AnalysisInputError, match=r"at least one dataset"):
        run_backtest([], config)
    with pytest.raises(AnalysisInputError, match=r"ReplayDataset"):
        run_backtest(["BTCUSDT"], config)  # type: ignore[list-item]
    with pytest.raises(AnalysisInputError, match=r"BacktestConfiguration"):
        run_backtest([dataset()], None)  # type: ignore[arg-type]


def test_run_backtest_rejects_non_iterable_datasets() -> None:
    with pytest.raises(AnalysisInputError, match=r"iterable of ReplayDataset"):
        run_backtest(7, configuration())  # type: ignore[arg-type]


def test_multi_symbol_backtest_partitions_by_symbol() -> None:
    config = configuration()
    result = run_backtest([dataset(), dataset(symbol="ETHUSDT")], config)
    assert len(result.signals) == 8
    assert [bucket.name for bucket in result.performance.by_symbol] == ["BTCUSDT", "ETHUSDT"]
    assert [bucket.total_buy_signals for bucket in result.performance.by_symbol] == [4, 4]
    assert result.performance.overall.total_buy_signals == 8
    ids = [row.signal_id for row in result.signals]
    assert len(set(ids)) == 8


def test_multi_timeframe_backtest_partitions_by_timeframe() -> None:
    config = configuration()
    thirty = dataset(timeframe="30m")
    result = run_backtest([dataset(), thirty], config)
    assert [bucket.name for bucket in result.performance.by_timeframe] == ["15m", "30m"]
    totals = {bucket.name: bucket.total_buy_signals for bucket in result.performance.by_timeframe}
    assert totals == {"15m": 4, "30m": 4}
    assert result.performance.overall.total_buy_signals == 8
    assert {row.timeframe for row in result.signals} == {"15m", "30m"}


def test_rows_are_sorted_chronologically_across_datasets() -> None:
    config = configuration()
    result = run_backtest([dataset(), dataset(symbol="ETHUSDT"), dataset(timeframe="30m")], config)
    ordering = [(row.opened_at, row.signal_id) for row in result.signals]
    assert ordering == sorted(ordering)


def test_buy_publications_match_the_documented_baseline() -> None:
    result = report()
    assert [row.candle_index for row in result.signals] == [4, 8, 12, 16]
    assert [row.score_total for row in result.signals] == [25, 25, 25, 25]
    assert [row.outcome_status for row in result.signals] == [
        OutcomeStatus.WIN,
        OutcomeStatus.OPEN,
        OutcomeStatus.OPEN,
        OutcomeStatus.OPEN,
    ]


def test_horizon_shortens_finalization_consistently() -> None:
    config = configuration(horizon=3)
    result = replay_history(dataset(), config)
    rows = [step for step in result.steps if step.signal.status.value == "BUY_SIGNAL"]
    finalized = [step.outcome.completed for step in result.steps if step.outcome.completed]
    # The index-4 buy finalizes at candle 7 with the shorter horizon.
    assert any(
        record.status is OutcomeStatus.WIN and record.final_index == 7
        for batch in finalized
        for record in batch
    )
    assert len(rows) == 4


def test_default_backtest_replays_all_candles_before_reporting() -> None:
    result = report()
    assert result.replays[0].steps[-1].index == 16
    assert len(buy_frames(result.replays[0])) == 4
