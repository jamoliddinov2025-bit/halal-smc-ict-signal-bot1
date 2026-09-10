"""Phase 26A: one signal is one outcome, and one final is counted exactly once.

Duplicate observations (replayed publications) and duplicate finalizations
(repeated evaluator records) must be idempotent: the ledger, the strategy
statistics, and the monthly report never count the same signal or outcome
twice. A finalization that conflicts with the recorded final is rejected.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import OutcomeTrackingAnalyzer
from smcsignal.analytics import AnalyticsObserver
from tests.analytics.helpers import buy_frames, observed_engine, publish


def _finalized_records(frames):
    tracker = OutcomeTrackingAnalyzer()
    finals: list = []
    for frame in frames:
        finals.extend(tracker.update(frame).completed)
    return tuple(finals)


def test_duplicate_observation_is_idempotent() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    buys = buy_frames(frames)

    for frame in buys:  # replay every published frame a second time
        repeated = observer.observe(frame)
        assert repeated is not None
    assert len(observer.observations) == len(buys)
    stats = observer.strategy_stats
    assert stats.total_buy_signals == len(buys)
    assert stats.open_count == len(buys)
    assert stats.finalized_count == 0


def test_batch_replay_cannot_double_count() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    observer.observe_all((*frames, *frames))
    assert observer.strategy_stats.total_buy_signals == len(buy_frames(frames))


def test_duplicate_finalization_counts_exactly_once() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    finals = _finalized_records(frames)
    assert finals, "the default fixture finalizes the index-4 publication"

    assert observer.record_finalized(finals[0]) is True
    snapshot_once = (observer.strategy_stats, observer.monthly_report())
    assert observer.record_finalized(finals[0]) is False
    assert observer.strategy_stats == snapshot_once[0]
    assert observer.monthly_report() == snapshot_once[1]

    stats = observer.strategy_stats
    assert stats.finalized_count == 1
    assert stats.win_count == 1
    assert stats.total_buy_signals == len(buy_frames(frames))
    assert stats.open_count == len(buy_frames(frames)) - 1


def test_conflicting_duplicate_finalization_is_rejected() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    final = _finalized_records(frames)[0]
    assert observer.record_finalized(final) is True

    forged = replace(final, mfe_price=final.mfe_price + Decimal(1))
    assert forged.outcome_id == final.outcome_id and forged != final
    with pytest.raises(AnalysisInputError, match="conflicting duplicate"):
        observer.record_finalized(forged)
    assert observer.finalized_outcomes == (final,)
    assert observer.strategy_stats.win_count == 1


def test_unobserved_final_cannot_enter_the_ledger() -> None:
    adapter, eligible = observed_engine()
    frames = publish(adapter, eligible)
    final = _finalized_records(frames)[0]

    stranger = AnalyticsObserver()
    with pytest.raises(AnalysisInputError, match="publication boundary"):
        stranger.record_finalized(final)
    assert stranger.strategy_stats.finalized_count == 0
