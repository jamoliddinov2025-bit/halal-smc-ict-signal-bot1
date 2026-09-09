"""Walk-forward evaluation facts, bucket composition, and degradation tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis.backtest import ReplayDataset
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.outcome_tracking.models import OutcomeStatus
from smcsignal.analysis.robustness import (
    evaluate_dataset,
    machine_summary,
    run_robustness,
)
from smcsignal.analysis.robustness.models import SegmentStatus
from tests.robustness.helpers import (
    DATASET_ID,
    EIGHT,
    RISE_THEN_FALL,
    bars,
    configuration,
    higher_candles,
    rich_dataset,
    robustness,
)


def test_rich_fixture_window_facts_are_hand_verified() -> None:
    result = evaluate_dataset(rich_dataset(), configuration(), robustness())
    assert len(result.windows) == 3
    window = result.windows[0]
    assert window.development.summary.total_buy_signals == 2
    assert window.development.summary.finalized_count == 2
    assert window.validation.summary.total_buy_signals == 2
    assert window.validation.summary.finalized_count == 1
    assert window.validation.summary.win_rate == Decimal(1)
    statuses = [w.validation.status for w in result.windows]
    assert statuses == [
        SegmentStatus.STABLE,
        SegmentStatus.UNDERSAMPLED,
        SegmentStatus.WEAK,
    ]


def test_development_precedes_validation_inside_every_window() -> None:
    result = evaluate_dataset(rich_dataset(), configuration(), robustness())
    for window in result.windows:
        dev_indexes = [row.candle_index for row in window.development.rows]
        val_indexes = [row.candle_index for row in window.validation.rows]
        assert all(index < 12 for index in dev_indexes)
        assert all(12 <= index < 26 for index in val_indexes)


def test_validation_segments_never_overlap_across_windows() -> None:
    result = evaluate_dataset(rich_dataset(), configuration(), robustness())
    validation_ranges = [
        (w.window.validation.start, w.window.validation.end) for w in result.windows
    ]
    for earlier, later in zip(validation_ranges, validation_ranges[1:], strict=False):
        assert later[0] >= earlier[1]


def test_open_and_finalized_outcomes_stay_separated() -> None:
    result = evaluate_dataset(rich_dataset(), configuration(), robustness())
    for window in result.windows:
        for segment in (window.development, window.validation):
            summary = segment.summary
            for row in segment.rows:
                assert row.outcome.status is OutcomeStatus.OPEN or (row.final_return is not None)
            assert summary.open_count == sum(
                row.outcome.status is OutcomeStatus.OPEN for row in segment.rows
            )
            assert summary.finalized_count == len(segment.rows) - summary.open_count


def test_overall_bucket_covers_validation_rows_only() -> None:
    dataset = rich_dataset()
    result = evaluate_dataset(dataset, configuration(), robustness())
    report = run_robustness([dataset], configuration(), robustness())
    validation_total = sum(w.validation.summary.total_buy_signals for w in result.windows)
    development_total = sum(w.development.summary.total_buy_signals for w in result.windows)
    assert development_total > 0  # the fixture really publishes in-window
    assert report.overall.total_buy_signals == validation_total
    assert report.overall.total_buy_signals != development_total + validation_total


def test_symbol_and_timeframe_buckets_partition_validation_rows() -> None:
    first = rich_dataset(symbol="BTCUSDT")
    second = rich_dataset(symbol="ETHUSDT")
    report = run_robustness([first, second], configuration(), robustness())
    assert [bucket.name for bucket in report.by_symbol] == ["BTCUSDT", "ETHUSDT"]
    assert [bucket.name for bucket in report.by_timeframe] == ["15m"]
    assert sum(bucket.total_buy_signals for bucket in report.by_symbol) == (
        report.overall.total_buy_signals
    )
    assert report.by_symbol[0].total_buy_signals == report.by_symbol[1].total_buy_signals


def test_combination_buckets_reuse_the_phase19_shape() -> None:
    report = run_robustness([rich_dataset()], configuration(), robustness())
    assert report.by_combination
    combinations = {bucket.name for bucket in report.by_combination}
    row_keys = {row.combination_key for row in report.datasets[0].validation_rows}
    assert combinations == row_keys
    assert sum(bucket.total_buy_signals for bucket in report.by_combination) == (
        report.overall.total_buy_signals
    )


def test_period_rows_list_every_validation_segment_with_dataset_key() -> None:
    first = rich_dataset(symbol="BTCUSDT")
    second = rich_dataset(symbol="ETHUSDT")
    report = run_robustness([second, first], configuration(), robustness())
    assert [row.dataset_key.split(":")[0] for row in report.by_period] == [
        "BTCUSDT",
        "BTCUSDT",
        "BTCUSDT",
        "ETHUSDT",
        "ETHUSDT",
        "ETHUSDT",
    ]
    assert all(row.segment.kind == "validation" for row in report.by_period)


def test_regime_listings_group_validation_rows_by_causal_regime() -> None:
    report = run_robustness([rich_dataset()], configuration(), robustness())
    labels = [segment.label for segment in report.by_regime]
    assert labels == sorted(labels)
    total = sum(segment.summary.total_buy_signals for segment in report.by_regime)
    assert total == report.overall.total_buy_signals


def test_regime_annotations_cover_every_window_row() -> None:
    result = evaluate_dataset(rich_dataset(), configuration(), robustness())
    for window in result.windows:
        for segment in (window.development, window.validation):
            for row in segment.rows:
                assert row.signal_id in result.row_regimes


def test_degradation_reports_exact_decimal_deltas() -> None:
    report = run_robustness([rich_dataset()], configuration(), robustness())
    first = report.datasets[0].windows[0]
    expected = first.validation.summary.win_rate - first.development.summary.win_rate
    assert first.win_rate_degradation == expected
    assert report.degradation.window_count == 3
    assert report.degradation.comparable_window_count == 2
    assert report.degradation.average_win_rate_degradation == Decimal(0)


def test_stability_summary_counts_statuses_and_spreads() -> None:
    report = run_robustness([rich_dataset()], configuration(), robustness())
    stability = report.stability
    assert stability.validation_segment_count == 3
    assert stability.sufficient_segment_count == 2
    assert stability.win_rate_spread == Decimal(1)
    assert stability.status is SegmentStatus.WEAK
    assert stability.best_period == "2024-01"
    assert stability.worst_period == "2024-01"


def test_minimum_samples_are_configurable_gates() -> None:
    # Raising the finalized minimum past every segment marks all UNDERSAMPLED.
    config = robustness(minimum_finalized_for_stability=99)
    report = run_robustness([rich_dataset()], configuration(), config)
    assert report.stability.sufficient_segment_count == 0
    assert report.stability.status is SegmentStatus.UNDERSAMPLED
    assert all(row.segment.status is SegmentStatus.UNDERSAMPLED for row in report.by_period)
    # The same raise empties comparable degradation windows.
    assert report.degradation.comparable_window_count == 0
    assert report.degradation.average_win_rate_degradation is None


def test_minimum_windows_gate_marks_small_plans_undersampled() -> None:
    config = robustness(minimum_windows_for_stability=9)
    report = run_robustness([rich_dataset()], configuration(), config)
    assert report.stability.sufficient_segment_count == 2
    assert report.stability.status is SegmentStatus.UNDERSAMPLED


def test_zero_signal_dataset_is_a_valid_robustness_report() -> None:
    # Raising both publish thresholds to the maximum publishes nothing: a
    # zero-signal walk-forward plan is still a valid, fully rendered report.
    base = configuration()
    strict = replace(
        base,
        setup_quality=replace(base.setup_quality, publish_threshold=100),
        signal_engine=replace(base.signal_engine, publish_threshold=100),
    )
    report = run_robustness([rich_dataset()], strict, robustness())
    assert report.overall.total_buy_signals == 0
    assert report.overall.win_rate is None
    assert report.by_symbol == ()
    assert report.by_timeframe == ()
    assert report.by_combination == ()
    assert report.stability.status is SegmentStatus.UNDERSAMPLED
    assert report.degradation.comparable_window_count == 0
    machine = machine_summary(report)
    assert b'"total_buy_signals":0,"win_count":0,"win_rate":null' in machine


def test_multi_symbol_evaluation_is_deterministic() -> None:
    datasets = [rich_dataset(symbol="BTCUSDT"), rich_dataset(symbol="ETHUSDT")]
    first = run_robustness(datasets, configuration(), robustness())
    second = run_robustness(datasets, configuration(), robustness())
    assert first == second
    assert first.report_id == second.report_id


def test_multi_timeframe_evaluation_is_deterministic() -> None:
    primary = rich_dataset(timeframe="15m")
    slower = rich_dataset(timeframe="30m")
    report = run_robustness([primary, slower], configuration(), robustness())
    assert [bucket.name for bucket in report.by_timeframe] == ["15m", "30m"]
    repeated = run_robustness([primary, slower], configuration(), robustness())
    assert report == repeated


def test_dataset_order_does_not_change_the_report_identity() -> None:
    first = rich_dataset(symbol="BTCUSDT")
    second = rich_dataset(symbol="ETHUSDT")
    one = run_robustness([first, second], configuration(), robustness())
    two = run_robustness([second, first], configuration(), robustness())
    assert one == two
    assert one.report_id == two.report_id


def test_duplicate_datasets_are_rejected() -> None:
    with pytest.raises(AnalysisInputError, match=r"exactly once"):
        run_robustness([rich_dataset(), rich_dataset()], configuration(), robustness())


def test_too_short_dataset_is_rejected_with_the_missing_bars() -> None:
    dataset = rich_dataset(prices=tuple(range(20, 40)))
    with pytest.raises(AnalysisInputError, match=r"shorter than one walk-forward window"):
        evaluate_dataset(dataset, configuration(), robustness())


def test_empty_dataset_list_is_rejected() -> None:
    with pytest.raises(AnalysisInputError, match=r"at least one dataset"):
        run_robustness([], configuration(), robustness())


def test_non_dataset_inputs_are_rejected() -> None:
    with pytest.raises(AnalysisInputError):
        run_robustness(["dataset"], configuration(), robustness())  # type: ignore[list-item]
    with pytest.raises(AnalysisInputError):
        evaluate_dataset("dataset", configuration(), robustness())  # type: ignore[arg-type]
    with pytest.raises(AnalysisConfigurationError):
        run_robustness([rich_dataset()], configuration(), "robustness")  # type: ignore[arg-type]


def test_inputs_are_never_mutated_by_evaluation() -> None:
    dataset = rich_dataset()
    candles_before = list(dataset.candles)
    higher_before = {key: list(value) for key, value in dataset.higher_candles.items()}
    run_robustness([dataset], configuration(), robustness())
    assert list(dataset.candles) == candles_before
    assert {key: list(value) for key, value in dataset.higher_candles.items()} == (higher_before)


def test_fall_fixture_changes_only_late_window_facts() -> None:
    report = run_robustness([rich_dataset(prices=RISE_THEN_FALL)], configuration(), robustness())
    # the early window matches the rich fixture; later windows publish less
    assert report.datasets[0].windows[0].validation.status is SegmentStatus.STABLE
    assert [w.validation.summary.total_buy_signals for w in report.datasets[0].windows] == [2, 0, 0]


def test_series_keys_are_sorted_and_cover_the_datasets() -> None:
    first = rich_dataset(symbol="BTCUSDT")
    second = rich_dataset(symbol="ETHUSDT")
    report = run_robustness([first, second], configuration(), robustness())
    assert list(report.series_keys) == sorted(report.series_keys)
    assert len(report.series_keys) == 2
    assert report.series_keys[0].startswith("BTCUSDT:15m:")


def test_direct_dataset_construction_keeps_timeframe_out_of_mtf_config() -> None:
    # A 5m dataset built directly (not via the mtf-routed helper) still replays.
    dataset = ReplayDataset(
        symbol="BTCUSDT",
        timeframe="5m",
        candles=bars("5m", tuple(range(20, 80)), start=EIGHT),
        higher_candles=higher_candles(),
        dataset_id=DATASET_ID,
    )
    result = evaluate_dataset(dataset, configuration(), robustness())
    assert result.windows
