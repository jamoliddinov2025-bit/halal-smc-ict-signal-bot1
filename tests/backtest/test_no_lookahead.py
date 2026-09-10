"""Strict anti-lookahead proofs for the historical replay layer.

These tests prove, structurally and by shock, that replayed historical facts
never depend on candles beyond their own cutoff: the engine never reads a
primary candle beyond its draw cursor, published HTF evidence was already
available before the primary candle opened, prefixes reproduce exactly, and
alternate futures cannot change past signals.
"""

from __future__ import annotations

from datetime import datetime

from smcsignal.analysis.backtest import (
    HistoricalReplay,
    replay_history,
    run_backtest,
)
from smcsignal.analysis.outcome_tracking import (
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)
from smcsignal.analysis.signal_engine import SignalStatus
from tests.backtest.helpers import bars, configuration, dataset


class AccessLoggingCandles(tuple):
    """A candle tuple that records every indexed read after construction."""

    def __new__(cls, candles):
        instance = super().__new__(cls, candles)
        instance.reads = []
        return instance

    def __getitem__(self, index):  # type: ignore[override]
        if isinstance(index, int):
            self.reads.append(index)
        return super().__getitem__(index)


def _signal_facts(result):
    """(candle_index, signal_id, score, setup_identity) of every published BUY."""

    return tuple(
        (
            step.signal.candidate.signal.candle.candle_index,
            step.signal.signal_id,
            step.signal.candidate.signal.score_total,
            step.signal.setup_identity,
        )
        for step in result.steps
        if step.signal.status is SignalStatus.BUY_SIGNAL
    )


def test_engine_never_reads_a_primary_candle_beyond_the_cursor() -> None:
    from smcsignal.analysis.backtest import ReplayDataset

    candles = AccessLoggingCandles(bars("15m", tuple(range(20, 37))))
    higher = dataset().higher_candles
    instance = ReplayDataset("BTCUSDT", "15m", candles, higher)
    assert candles.reads == []  # dataset validation iterates; the engine indexes
    engine = HistoricalReplay(instance, configuration())
    assert candles.reads == []  # construction reads no primary candle
    for expected in range(len(candles)):
        engine.update()
        assert max(candles.reads) == expected
        assert all(index <= expected for index in candles.reads)
    assert engine.complete


def test_published_htf_evidence_was_available_before_the_primary_open() -> None:
    result = replay_history(dataset(), configuration())
    for step in result.steps:
        mtf = step.signal.upstream.upstream.upstream.upstream
        opened_at = step.signal.candidate.signal.candle.opened_at
        for relation in mtf.relations:
            for reference in relation.evidence:
                assert isinstance(reference.source.available_at, datetime)
                assert reference.source.available_at <= opened_at


def test_alternate_futures_cannot_change_past_signals() -> None:
    config = configuration()
    head = tuple(range(20, 30))
    rising_tail = (*head, 40, 41, 42, 43, 44, 45, 46)
    falling_tail = (*head, 10, 9, 8, 7, 6, 5, 4)
    higher = dataset().higher_candles
    # Both futures are alternate continuations of one declared series identity.
    rising = run_backtest([dataset(prices=rising_tail, higher=higher)], config)
    falling = run_backtest([dataset(prices=falling_tail, higher=higher)], config)
    head_a = [fact for fact in _signal_facts(rising.replays[0]) if fact[0] < len(head)]
    head_b = [fact for fact in _signal_facts(falling.replays[0]) if fact[0] < len(head)]
    assert head_a  # the head publishes signals
    assert head_a == head_b  # and they are byte-identical across futures
    statuses_a = {row.signal_id: row.outcome_status for row in rising.signals}
    statuses_b = {row.signal_id: row.outcome_status for row in falling.signals}
    assert statuses_a != statuses_b  # only future outcomes may differ


def test_outcomes_equal_the_standalone_phase_18_replay() -> None:
    config = configuration()
    result = replay_history(dataset(), config)
    standalone = analyze_outcome_tracking(
        result.signal_frames, OutcomeTrackingConfig(horizon_bars=10)
    )
    assert result.outcomes == standalone


def test_horizon_behavior_matches_phase_18_semantics() -> None:
    config = configuration()
    result = replay_history(dataset(), config)
    # The index-4 buy (reference close 24) finalizes on candle 14 (close 34).
    win = result.steps[14].outcome.completed
    assert len(win) == 1
    assert win[0].final_index == 14
    assert win[0].status.value == "WIN"
    # Open outcomes at end-of-series stay open: no flush exists.
    last = result.steps[-1].outcome
    assert last.analytics.open_count == 3
    assert last.analytics.finalized_count == 1


def test_chronological_order_is_enforced_by_the_dataset_contract() -> None:
    import pytest

    from smcsignal.analysis.backtest import ReplayDataset
    from smcsignal.analysis.errors import AnalysisInputError

    candles = bars("15m", tuple(range(20, 25)))
    with pytest.raises(AnalysisInputError, match=r"strictly chronological"):
        ReplayDataset("BTCUSDT", "15m", (*candles[:3], candles[1], candles[4]))


def test_replay_does_not_mutate_its_inputs() -> None:
    from copy import deepcopy

    config = configuration()
    instance = dataset()
    candles_before = deepcopy(instance.candles)
    higher_before = {key: deepcopy(value) for key, value in instance.higher_candles.items()}
    result = replay_history(instance, config)
    assert instance.candles == candles_before
    assert dict(instance.higher_candles) == higher_before
    assert instance == dataset()
    assert config == configuration()
    # Canonical facts pass through by identity, not by copy-and-rewrite.
    for step in result.steps:
        assert step.outcome.upstream is step.signal
        assert step.attribution.upstream is step.signal


def test_signal_frames_equal_the_direct_phase_17_chain() -> None:
    from tests.outcome_tracking.helpers import signal_frames

    result = replay_history(dataset(), configuration())
    assert result.signal_frames == signal_frames()


def test_replayed_rows_reference_the_published_outcome_records() -> None:
    result = run_backtest([dataset()], configuration())
    published = {
        record.outcome_id: record
        for step in result.replays[0].steps
        for record in (*step.outcome.created, *step.outcome.evaluated)
    }
    for row in result.signals:
        assert row.outcome is published[row.outcome_id]  # the latest version, by identity
