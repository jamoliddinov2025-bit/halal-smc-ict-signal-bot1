from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.performance import PerformanceReport
from smcsignal.analysis.review import (
    ReviewConfig,
    build_monthly_reviews,
    render_review_text,
)
from tests.review.helpers import (
    run,
    single_month_report,
    text,
    two_month_report,
    zero_signal_report,
)

GOLDEN = """SMC/ICT signal bot monthly review (monthly-review-v1)
descriptive statistics of published signal records; not advice
generated 2024-03-01T00:00:00Z

month 2024-01: signals 4, open 0, finalized 4 (win 4, loss 0, flat 0)
  win_rate 1
  average_final_return 0.34102182539682539682539682539682539682539682539683
  average_mfe_return 0.37512400793650793650793650793650793650793650793651
  average_mae_return 0
  comparison: none (first reviewed month)

month 2024-02: signals 1, open 0, finalized 1 (win 1, loss 0, flat 0)
  win_rate 1
  average_final_return 0.5
  average_mfe_return 0.55
  average_mae_return 0
  comparison with 2024-01: suppressed (samples 1 and 4 finalized; minimum 10)
"""


def test_review_text_is_the_golden_plain_text() -> None:
    assert text() == GOLDEN


def test_relaxed_minimum_quotes_exact_deltas() -> None:
    reviews = run(config=ReviewConfig(minimum_finalized_for_comparison=1))
    comparison = reviews[1].comparison
    assert comparison is not None
    assert comparison.both_sufficient is True
    assert comparison.win_rate_delta == Decimal(0)
    assert comparison.average_final_return_delta == Decimal("0.5") - Decimal(
        "0.34102182539682539682539682539682539682539682539683"
    )
    rendered = render_review_text(reviews, generated_at=datetime(2024, 3, 1, tzinfo=UTC))
    assert "comparison with 2024-01: win_rate delta 0" in rendered
    assert "samples 1 and 4 finalized)" in rendered


def test_reviews_follow_chronological_report_months() -> None:
    reviews = run()
    assert [review.month for review in reviews] == ["2024-01", "2024-02"]
    assert reviews[0].previous_bucket is None
    assert reviews[1].previous_bucket is reviews[0].bucket  # copied verbatim


def test_single_month_report_has_no_comparison() -> None:
    reviews = build_monthly_reviews(single_month_report())
    assert len(reviews) == 1
    assert reviews[0].month == "2024-01"
    assert reviews[0].comparison is None
    assert reviews[0].bucket.total_buy_signals == 4


def test_zero_signal_reports_cannot_be_reviewed() -> None:
    with pytest.raises(AnalysisInputError):
        build_monthly_reviews(zero_signal_report())


def test_reviews_require_a_performance_report() -> None:
    with pytest.raises(AnalysisInputError):
        build_monthly_reviews("report")  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        build_monthly_reviews(two_month_report(), config="strict")  # type: ignore[arg-type]


def test_reviews_are_deterministic_and_inputs_untouched() -> None:
    report = two_month_report()
    saved = deepcopy(report)
    first = build_monthly_reviews(report)
    second = build_monthly_reviews(report)
    assert first == second
    assert [review.review_id for review in first] == [review.review_id for review in second]
    assert report == saved
    assert isinstance(report, PerformanceReport)


def test_rendering_requires_reviews_and_utc_timestamps() -> None:
    reviews = run()
    with pytest.raises(AnalysisInputError):
        render_review_text(())
    with pytest.raises(AnalysisInputError):
        render_review_text("reviews")  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        render_review_text(reviews, generated_at=datetime(2024, 3, 1))
    naive_tuple = (*reviews, "not-a-review")
    with pytest.raises(AnalysisInputError):
        render_review_text(naive_tuple)  # type: ignore[arg-type]


def test_rendered_text_always_shows_sample_sizes() -> None:
    rendered = text()
    assert "signals 4, open 0, finalized 4" in rendered
    assert "signals 1, open 0, finalized 1" in rendered
    assert "samples 1 and 4 finalized; minimum 10" in rendered
    assert rendered.endswith(")\n")
    assert "advice" in rendered  # the not-advice disclaimer stays visible


def test_review_text_changes_only_with_its_inputs() -> None:
    baseline = text()
    later = render_review_text(run(), generated_at=datetime(2024, 4, 1, tzinfo=UTC))
    assert later != baseline
    assert "generated 2024-04-01T00:00:00Z" in later
    assert text() == baseline  # rendering never mutates the reviews
