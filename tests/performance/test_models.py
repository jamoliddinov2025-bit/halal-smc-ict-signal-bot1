from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.performance import analyze_performance
from smcsignal.analysis.performance.models import (
    GROUPS,
    NO_LABELS_KEY,
    PerformanceBucket,
)
from tests.performance.helpers import run


def overall() -> PerformanceBucket:
    return run().overall


def test_groups_are_the_closed_whitelist() -> None:
    assert GROUPS == (
        "overall",
        "symbol",
        "timeframe",
        "label",
        "combination",
        "month",
    )
    assert NO_LABELS_KEY == "no_labels"


def test_bucket_counts_must_partition() -> None:
    bucket = overall()
    with pytest.raises(AnalysisInputError):
        replace(bucket, open_count=bucket.open_count + 1)
    with pytest.raises(AnalysisInputError):
        replace(bucket, win_count=bucket.win_count + 1)
    with pytest.raises(AnalysisInputError):
        replace(bucket, total_buy_signals=-1)
    with pytest.raises(AnalysisInputError):
        replace(bucket, finalized_count=2)  # 2 + 3 != 4


def test_bucket_rates_are_undefined_without_finalized_outcomes() -> None:
    bucket = overall()
    open_only = replace(
        bucket,
        total_buy_signals=3,
        open_count=3,
        win_count=0,
        loss_count=0,
        flat_count=0,
        finalized_count=0,
        final_return_sum=Decimal(0),
        mfe_return_sum=Decimal(0),
        mae_return_sum=Decimal(0),
        win_rate=None,
        average_final_return=None,
        average_mfe_return=None,
        average_mae_return=None,
    )
    assert open_only.win_rate is None
    with pytest.raises(AnalysisInputError):
        replace(open_only, win_rate=Decimal(1))


def test_bucket_win_rate_must_stay_between_zero_and_one() -> None:
    bucket = overall()
    with pytest.raises(AnalysisInputError):
        replace(bucket, win_rate=Decimal("1.5"))
    with pytest.raises(AnalysisInputError):
        replace(bucket, win_rate=Decimal(-1))


def test_bucket_sums_must_be_finite_decimals() -> None:
    bucket = overall()
    with pytest.raises(AnalysisInputError):
        replace(bucket, final_return_sum=Decimal("NaN"))
    with pytest.raises(AnalysisInputError):
        replace(bucket, final_return_sum="0.5")  # type: ignore[arg-type]


def test_bucket_group_and_name_are_validated() -> None:
    bucket = overall()
    with pytest.raises(AnalysisInputError):
        replace(bucket, group="strategy")
    with pytest.raises(AnalysisInputError):
        replace(bucket, name=" padded ")
    with pytest.raises(AnalysisInputError):
        replace(bucket, name="")
    with pytest.raises(AnalysisInputError):
        replace(bucket, sufficient_sample=1)  # type: ignore[arg-type]


def multi_symbol_report():
    from tests.performance.helpers import eth_replay, flat_replay

    btc_out, btc_attr = flat_replay()
    eth_out, eth_attr = eth_replay()
    return analyze_performance({"btc": btc_out, "eth": eth_out}, {"btc": btc_attr, "eth": eth_attr})


def test_report_validates_bucket_groups_and_order() -> None:
    report = multi_symbol_report()
    assert [bucket.name for bucket in report.by_symbol] == ["BTCUSDT", "ETHUSDT"]
    with pytest.raises(AnalysisInputError):
        replace(report, by_symbol=report.by_symbol[::-1])
    with pytest.raises(AnalysisInputError):
        replace(report, by_symbol=report.by_label)
    with pytest.raises(AnalysisInputError):
        replace(report, by_month=(*report.by_month, replace(report.by_month[0], name="2024-1")))
    with pytest.raises(AnalysisInputError):
        replace(report, by_label=report.by_combination)


def test_report_validates_the_overall_bucket() -> None:
    report = run()
    with pytest.raises(AnalysisInputError):
        replace(report, overall=replace(report.overall, group="symbol"))
    with pytest.raises(AnalysisInputError):
        replace(report, overall=replace(report.overall, name="everything"))


def test_report_buckets_must_partition_the_overall_population() -> None:
    report = run()
    extra = replace(report.by_symbol[0], total_buy_signals=99, open_count=98, win_count=1)
    with pytest.raises(AnalysisInputError):
        replace(report, by_symbol=(extra,))


def test_report_validates_series_keys_and_identity() -> None:
    report = run()
    with pytest.raises(AnalysisInputError):
        replace(report, series_keys=("btc", "btc"))
    with pytest.raises(AnalysisInputError):
        replace(report, series_keys=("btc", " eth"))
    with pytest.raises(AnalysisInputError):
        replace(report, series_keys=())
    with pytest.raises(AnalysisInputError):
        replace(report, report_id="report:abc")
    with pytest.raises(AnalysisInputError):
        replace(report, report_id=" padded ")


def test_report_validates_ranked_combinations() -> None:
    report = run()  # no sufficient buckets under the default minimum
    assert report.best_combination is None and report.worst_combination is None
    with pytest.raises(AnalysisInputError):
        replace(report, best_combination=report.by_combination[0])
    with pytest.raises(AnalysisInputError):
        replace(report, worst_combination=report.by_combination[0])


def test_sufficient_sample_must_match_the_configured_minimum() -> None:
    report = run()
    mismatched = replace(
        report.by_symbol[0], sufficient_sample=not report.by_symbol[0].sufficient_sample
    )
    with pytest.raises(AnalysisInputError):
        replace(report, by_symbol=(mismatched,))


def test_month_names_use_the_utc_calendar_form() -> None:
    report = run()
    good = report.by_month[0]
    assert good.name == "2024-01"
    for bad in ("2024-1", "24-01", "2024/01", "2024-13", "2024-00", "202401"):
        with pytest.raises(AnalysisInputError):
            replace(report, by_month=(replace(good, name=bad),))
