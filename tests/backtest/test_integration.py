"""Canonical-flow integration: the replay composes the existing phases exactly."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.backtest import (
    replay_history,
    run_backtest,
    series_key_for,
)
from smcsignal.analysis.outcome_tracking import (
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)
from smcsignal.analysis.performance import (
    PerformanceConfig,
    analyze_performance,
)
from smcsignal.analysis.setup_attribution import (
    SetupAttributionConfig,
    analyze_setup_attribution,
)
from tests.backtest.helpers import configuration, dataset


def test_signal_engine_integration_matches_the_direct_chain() -> None:
    from tests.outcome_tracking.helpers import signal_frames

    result = replay_history(dataset(), configuration())
    assert result.signal_frames == signal_frames()
    buys = [frame for frame in result.signal_frames if frame.status.value == "BUY_SIGNAL"]
    assert [frame.candidate.signal.candle.candle_index for frame in buys] == [4, 8, 12, 16]
    assert [frame.candidate.signal.score_total for frame in buys] == [25, 25, 25, 25]


def test_outcome_tracking_integration_is_the_existing_replay() -> None:
    config = configuration()
    result = replay_history(dataset(), config)
    standalone = analyze_outcome_tracking(result.signal_frames, config.outcome_tracking)
    assert result.outcomes == standalone
    assert result.outcomes[14].completed[0].status.value == "WIN"
    assert result.outcomes[-1].analytics == standalone[-1].analytics


def test_setup_attribution_integration_is_the_existing_replay() -> None:
    result = replay_history(dataset(), configuration())
    standalone = analyze_setup_attribution(result.signal_frames, SetupAttributionConfig())
    assert result.attributions == standalone
    attributed = [snapshot.attribution for snapshot in result.attributions if snapshot.attribution]
    assert [profile.combination_key for profile in attributed] == ["mtf_bullish"] * 4


def test_performance_integration_is_the_existing_report() -> None:
    config = configuration()
    result = run_backtest([dataset()], config)
    key = series_key_for(dataset())
    standalone = analyze_performance(
        {key: result.replays[0].outcomes},
        {key: result.replays[0].attributions},
        PerformanceConfig(),
    )
    assert result.performance == standalone
    overall = result.performance.overall
    assert (overall.total_buy_signals, overall.open_count, overall.finalized_count) == (4, 3, 1)
    assert overall.win_rate == Decimal(1)
    assert overall.final_return_sum == Decimal(
        "0.41666666666666666666666666666666666666666666666667"
    )
    assert overall.mfe_return_sum == Decimal("0.45833333333333333333333333333333333333333333333333")
    assert overall.mae_return_sum == Decimal(0)


def test_rows_carry_attribution_and_outcome_facts_unchanged() -> None:
    result = run_backtest([dataset()], configuration())
    win = result.signals[0]
    assert win.candle_index == 4
    assert win.labels and win.labels[0].value == "mtf_bullish"
    assert win.combination_key == "mtf_bullish"
    assert win.reference_close == Decimal(24)
    assert win.final_index == 14
    assert win.final_close == Decimal(34)
    assert win.final_return == Decimal("0.41666666666666666666666666666666666666666666666667")
    assert win.mfe_return == Decimal("0.45833333333333333333333333333333333333333333333333")
    assert win.mae_return == Decimal(0)
    open_row = result.signals[1]
    assert open_row.finalized is False
    assert open_row.final_return is None
    assert open_row.mfe_return is not None  # partially evaluated extremes are visible


def test_outcome_tracking_configuration_flows_through_the_bundle() -> None:
    config = configuration(horizon=3)
    result = replay_history(dataset(), config)
    assert all(step.outcome.settings.horizon_bars == 3 for step in result.steps)
    standalone = analyze_outcome_tracking(
        result.signal_frames, OutcomeTrackingConfig(horizon_bars=3)
    )
    assert result.outcomes == standalone


def test_multi_dataset_rows_partition_the_performance_report() -> None:
    config = configuration()
    result = run_backtest([dataset(), dataset(symbol="ETHUSDT"), dataset(timeframe="30m")], config)
    overall = result.performance.overall
    assert overall.total_buy_signals == len(result.signals)
    finalized = sum(row.finalized for row in result.signals)
    assert (finalized, len(result.signals) - finalized) == (
        overall.finalized_count,
        overall.open_count,
    )
    symbols = {bucket.name: bucket.total_buy_signals for bucket in result.performance.by_symbol}
    assert symbols == {"BTCUSDT": 8, "ETHUSDT": 4}
    timeframes = {
        bucket.name: bucket.total_buy_signals for bucket in result.performance.by_timeframe
    }
    assert timeframes == {"15m": 8, "30m": 4}
    months = {bucket.name: bucket.total_buy_signals for bucket in result.performance.by_month}
    assert sum(months.values()) == overall.total_buy_signals


def test_combination_buckets_come_from_the_existing_attribution() -> None:
    result = run_backtest([dataset()], configuration())
    combinations = {
        bucket.name: bucket.total_buy_signals for bucket in result.performance.by_combination
    }
    assert combinations == {"mtf_bullish": 4}
    labels = {bucket.name: bucket.total_buy_signals for bucket in result.performance.by_label}
    assert labels == {"mtf_bullish": 4}
    assert result.performance.best_combination is None  # below the ranking minimum
