"""Walk-forward window planning tests: chronology, disjointness, warmup drop."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.robustness import (
    PeriodRange,
    WalkForwardWindow,
    plan_windows,
    split_rows,
)
from smcsignal.analysis.robustness.models import SegmentStats
from tests.robustness.helpers import bars, rich_dataset, robustness


def test_plan_windows_places_validation_periods_disjoint_and_in_order() -> None:
    windows = plan_windows(rich_dataset(), robustness())
    assert [window.window_index for window in windows] == [0, 1, 2]
    previous_end = -1
    for window in windows:
        assert window.validation.start == previous_end if previous_end >= 0 else True
        previous_end = window.validation.end
    # development precedes validation inside each window, without a gap
    for window in windows:
        assert window.development.end == window.validation.start
        assert window.window_start == window.development.start
        assert window.window_end == window.validation.end


def test_plan_windows_steps_by_the_configured_stride() -> None:
    windows = plan_windows(rich_dataset(), robustness())
    starts = [window.window_start for window in windows]
    assert starts == [0, 14, 28]
    assert all(later - earlier == 14 for earlier, later in zip(starts, starts[1:], strict=False))


def test_plan_windows_drops_a_trailing_partial_window() -> None:
    # 60 candles, window 26, step 14: candidate starts 0,14,28 fit (28+26=54<=60),
    # 42 does not (42+26=68>60).
    windows = plan_windows(rich_dataset(), robustness())
    assert len(windows) == 3
    assert windows[-1].window_end == 54


def test_plan_windows_is_empty_for_a_too_short_dataset() -> None:
    dataset = rich_dataset(prices=tuple(range(20, 40)))  # 20 candles < 12+14
    assert plan_windows(dataset, robustness()) == ()


def test_plan_windows_keeps_a_longer_history_within_one_month_label() -> None:
    windows = plan_windows(rich_dataset(), robustness())
    assert [window.validation.label for window in windows] == ["2024-01"] * 3


def test_plan_windows_is_deterministic() -> None:
    dataset = rich_dataset()
    config = robustness()
    assert plan_windows(dataset, config) == plan_windows(dataset, config)


def test_plan_windows_rejects_invalid_inputs() -> None:
    with pytest.raises(AnalysisInputError):
        plan_windows(rich_dataset(), "robustness")  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        plan_windows("dataset", robustness())  # type: ignore[arg-type]


def test_period_range_rejects_empty_and_negative_ranges() -> None:
    moment = datetime(2024, 1, 1, tzinfo=UTC)
    with pytest.raises(AnalysisInputError):
        PeriodRange(2, 2, moment)
    with pytest.raises(AnalysisInputError):
        PeriodRange(-1, 3, moment)


def test_period_range_label_uses_the_utc_calendar_month() -> None:
    moment = datetime(2024, 3, 31, 23, 45, tzinfo=UTC)
    assert PeriodRange(0, 1, moment).label == "2024-03"


def test_walk_forward_window_requires_contiguous_periods() -> None:
    moment = datetime(2024, 1, 1, tzinfo=UTC)
    development = PeriodRange(0, 10, moment)
    touching = PeriodRange(10, 20, moment)
    WalkForwardWindow(0, development, touching)
    with pytest.raises(AnalysisInputError, match=r"immediately follow"):
        WalkForwardWindow(0, development, PeriodRange(11, 20, moment))


def test_split_rows_partitions_by_the_development_boundary() -> None:
    from smcsignal.analysis.robustness import evaluate_dataset
    from tests.robustness.helpers import configuration

    result = evaluate_dataset(rich_dataset(), configuration(), robustness())
    window = result.windows[0]
    development, validation = split_rows(
        tuple(row for segment in (window.development, window.validation) for row in segment.rows),
        12,
    )
    assert development == window.development.rows
    assert validation == window.validation.rows


def test_split_rows_rejects_a_nonpositive_boundary() -> None:
    with pytest.raises(AnalysisInputError):
        split_rows((), 0)


def test_segment_stats_rejects_mismatched_rows_and_summary() -> None:
    from decimal import Decimal

    from smcsignal.analysis.outcome_tracking.models import AnalyticsSummary

    with pytest.raises(
        AnalysisInputError, match=r"segment summaries must cover exactly their rows"
    ):
        SegmentStats(
            kind="development",
            label="2024-01",
            regime=None,
            summary=AnalyticsSummary(
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
            ),
            status=None,
            rows=(),
        )


def test_bars_helper_yields_strictly_increasing_timestamps() -> None:
    candles = bars("15m", tuple(range(20, 40)), start=datetime(2024, 1, 1, tzinfo=UTC))
    timestamps = [candle.timestamp for candle in candles]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)


def test_exact_fit_history_yields_exactly_one_window() -> None:
    # 26 candles fit one 12+14 window exactly; nothing is dropped or wrapped.
    dataset = rich_dataset(prices=tuple(range(20, 46)))
    windows = plan_windows(dataset, robustness())
    assert len(windows) == 1
    (window,) = windows
    assert (window.window_start, window.window_end) == (0, 26)
    assert window.development.bars == 12 and window.validation.bars == 14


def test_one_candle_short_history_plans_no_windows() -> None:
    dataset = rich_dataset(prices=tuple(range(20, 45)))  # 25 candles, need 26
    assert plan_windows(dataset, robustness()) == ()
