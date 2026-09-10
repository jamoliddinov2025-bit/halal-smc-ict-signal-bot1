from __future__ import annotations

from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.displacement.calculation import ratio
from smcsignal.analysis.outcome_tracking import OutcomeStatus
from smcsignal.analysis.performance import (
    NO_LABELS_KEY,
    PerformanceConfig,
    analyze_performance,
)
from tests.performance.helpers import (
    eth_replay,
    falling_replay,
    february_replay,
    flat_replay,
    january_long_replay,
    rising_replay,
    sweep_long_replay,
)


def test_single_series_report_recomputes_exact_statistics() -> None:
    outcomes, attributions = rising_replay()
    report = analyze_performance({"btc": outcomes}, {"btc": attributions})
    overall = report.overall
    assert overall.group == "overall" and overall.name == "all"
    assert overall.total_buy_signals == 4
    assert overall.open_count == 3 and overall.finalized_count == 1
    assert (overall.win_count, overall.loss_count, overall.flat_count) == (1, 0, 0)
    assert overall.final_return_sum == ratio(Decimal(10), Decimal(24))
    assert overall.mfe_return_sum == ratio(Decimal(11), Decimal(24))
    assert overall.mae_return_sum == Decimal(0)
    assert overall.win_rate == Decimal(1)
    assert overall.average_final_return == ratio(Decimal(10), Decimal(24))
    assert overall.sufficient_sample is False  # 1 finalized < default minimum 10
    assert report.series_keys == ("btc",)
    assert report.outcome_settings.horizon_bars == 10
    assert report.attribution_settings is not None


def test_open_outcomes_never_touch_finalized_statistics() -> None:
    outcomes, attributions = rising_replay()
    report = analyze_performance({"btc": outcomes}, {"btc": attributions})
    # Only the index-4 WIN is finalized; three open outcomes stay out of sums.
    assert report.overall.final_return_sum == ratio(Decimal(10), Decimal(24))
    assert report.overall.finalized_count + report.overall.open_count == 4


def test_group_buckets_partition_the_population() -> None:
    outcomes, attributions = flat_replay()
    report = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert report.overall.total_buy_signals == 7
    assert [(bucket.name, bucket.total_buy_signals) for bucket in report.by_symbol] == [
        ("BTCUSDT", 7)
    ]
    assert [(bucket.name, bucket.total_buy_signals) for bucket in report.by_timeframe] == [
        ("15m", 7)
    ]
    assert [(bucket.name, bucket.total_buy_signals) for bucket in report.by_month] == [
        ("2024-01", 7)
    ]
    assert [(bucket.name, bucket.total_buy_signals) for bucket in report.by_label] == [
        ("pd_discount", 2),
        ("pd_premium", 4),
        ("mtf_bullish", 7),
    ]
    assert [
        (bucket.name, bucket.total_buy_signals, bucket.finalized_count)
        for bucket in report.by_combination
    ] == [
        ("mtf_bullish", 1, 1),
        ("pd_discount+mtf_bullish", 2, 0),
        ("pd_premium+mtf_bullish", 4, 0),
    ]
    flat = report.by_combination[0]
    assert flat.win_rate == Decimal(0)  # one FLAT finalization, no wins
    assert flat.average_final_return == Decimal(0)
    assert flat.average_mfe_return == ratio(Decimal(3), Decimal(24))
    assert flat.average_mae_return == ratio(Decimal(-3), Decimal(24))


def test_flat_and_loss_finalizations_are_copied_not_reclassified() -> None:
    outcomes, attributions = falling_replay()
    report = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert report.overall.total_buy_signals == 3
    assert report.overall.finalized_count == 1
    assert (report.overall.win_count, report.overall.loss_count) == (0, 1)
    assert report.overall.final_return_sum == ratio(Decimal(-10), Decimal(24))
    assert report.overall.win_rate == Decimal(0)


def test_mixed_statuses_recompute_every_aggregate() -> None:
    outcomes, attributions = flat_replay(horizon=3)
    report = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert report.outcome_settings.horizon_bars == 3
    assert report.overall.total_buy_signals == 7
    assert report.overall.finalized_count == 5 and report.overall.open_count == 2
    assert (report.overall.win_count, report.overall.loss_count) == (3, 2)
    assert report.overall.win_rate == ratio(Decimal(3), Decimal(5))


def test_reports_without_attribution_skip_label_buckets() -> None:
    outcomes, _ = rising_replay()
    report = analyze_performance({"btc": outcomes})
    assert report.attribution_settings is None
    assert report.by_label == () and report.by_combination == ()
    assert report.overall.total_buy_signals == 4  # signals still count overall


def test_multi_series_keys_merge_into_symbol_and_timeframe_buckets() -> None:
    btc_out, btc_attr = flat_replay()
    eth_out, eth_attr = eth_replay()
    report = analyze_performance(
        {"eth": eth_out, "btc": btc_out},  # unsorted input keys
        {"eth": eth_attr, "btc": btc_attr},
    )
    assert report.series_keys == ("btc", "eth")  # sorted deterministically
    assert [(b.name, b.total_buy_signals) for b in report.by_symbol] == [
        ("BTCUSDT", 7),
        ("ETHUSDT", 4),
    ]
    assert [(b.name, b.total_buy_signals) for b in report.by_timeframe] == [("15m", 11)]
    assert report.overall.total_buy_signals == 11
    assert report.overall.finalized_count == 2  # one per series


def test_month_buckets_use_utc_calendar_months() -> None:
    jan_out, jan_attr = january_long_replay()
    feb_out, feb_attr = february_replay()
    report = analyze_performance(
        {"jan": jan_out, "feb": feb_out}, {"jan": jan_attr, "feb": feb_attr}
    )
    assert [(b.name, b.total_buy_signals, b.finalized_count) for b in report.by_month] == [
        ("2024-01", 4, 4),
        ("2024-02", 1, 1),
    ]
    assert report.overall.finalized_count == 5 and report.overall.win_count == 5


def test_best_and_worst_combinations_respect_the_minimum() -> None:
    outcomes, attributions = sweep_long_replay()
    strict = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert strict.best_combination is None and strict.worst_combination is None
    ranked = analyze_performance(
        {"btc": outcomes},
        {"btc": attributions},
        PerformanceConfig(minimum_finalized_for_ranking=1),
    )
    assert ranked.best_combination is not None
    assert ranked.worst_combination is not None
    assert ranked.best_combination.name == "mtf_bullish"
    assert ranked.worst_combination.name == "bullish_displacement+sweep_associated+mtf_bullish"
    for bucket in ranked.by_combination:
        # Raw counts stay visible even when a group is never ranked.
        assert bucket.sufficient_sample == (bucket.finalized_count >= 1)


def test_empty_combination_keys_report_as_no_labels() -> None:
    assert NO_LABELS_KEY == "no_labels"


def test_report_identity_is_deterministic() -> None:
    outcomes, attributions = rising_replay()
    first = analyze_performance({"btc": outcomes}, {"btc": attributions})
    second = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert first == second
    assert first.report_id == second.report_id
    without = analyze_performance({"btc": outcomes})
    assert without.report_id != first.report_id
    assert first.report_id.startswith("performance-report:")


def test_same_signal_in_two_series_is_rejected() -> None:
    outcomes, attributions = rising_replay()
    with pytest.raises(AnalysisInputError):
        analyze_performance({"a": outcomes, "b": outcomes})


def test_input_validation_rejects_broken_replays() -> None:
    outcomes, attributions = rising_replay()
    with pytest.raises(AnalysisInputError):
        analyze_performance({})
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": ()})
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": (outcomes[1],)})  # not from index zero
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": outcomes[3:]})
    with pytest.raises(AnalysisInputError):
        analyze_performance({" btc": outcomes})  # untrimmed key
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": outcomes}, {"other": attributions})
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": outcomes}, {"btc": attributions[:5]})
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": outcomes}, {"btc": attributions[::-1]})
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": outcomes}, None, "strict")  # type: ignore[arg-type]


def test_misaligned_attribution_replay_is_rejected() -> None:
    outcomes, _ = rising_replay()
    _, other_attr = flat_replay()
    with pytest.raises(AnalysisInputError):
        analyze_performance({"btc": outcomes}, {"btc": other_attr})


def test_configuration_artifact_hash_is_stable() -> None:
    from smcsignal.analysis.performance import configuration_hash
    from smcsignal.analysis.performance.evidence import configuration_artifact

    config = PerformanceConfig()
    assert configuration_hash(config) == configuration_hash(config)
    assert len(configuration_hash(config)) == 64
    assert b'"reclassification":false' in configuration_artifact(config)
    assert b'"advice":false' in configuration_artifact(config)


def test_phase18_analytics_and_report_agree_on_finalized_counts() -> None:
    outcomes, _ = rising_replay()
    report = analyze_performance({"btc": outcomes})
    summary = outcomes[-1].analytics
    assert report.overall.total_buy_signals == summary.total_buy_signals
    assert report.overall.open_count == summary.open_count
    assert report.overall.finalized_count == summary.finalized_count
    assert report.overall.win_count == summary.win_count
    assert report.overall.final_return_sum == summary.final_return_sum
    assert report.overall.win_rate == summary.win_rate


def test_latest_outcomes_returns_terminal_versions() -> None:
    from smcsignal.analysis.performance import latest_outcomes

    outcomes, _ = flat_replay(horizon=3)
    records = latest_outcomes(outcomes)
    assert len(records) == 7  # one per BUY signal
    assert all(
        record.status is OutcomeStatus.OPEN or record.candles_observed == 3 for record in records
    )
    assert sum(record.status is OutcomeStatus.OPEN for record in records) == 2
