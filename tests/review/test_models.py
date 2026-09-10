from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.review import (
    MonthComparison,
    MonthlyReview,
    ReviewConfig,
    previous_month,
    reviewed_at_text,
)
from tests.review.helpers import run


def test_previous_month_rolls_years_and_months() -> None:
    assert previous_month("2024-01") == "2023-12"
    assert previous_month("2024-12") == "2024-11"
    assert previous_month("2025-03") == "2025-02"
    for bad in ("2024-1", "24-01", "2024-13", "2024/01", " 2024-01", "2024-01 "):
        with pytest.raises(AnalysisInputError):
            previous_month(bad)


def test_reviewed_at_text_uses_the_utc_form() -> None:
    moment = datetime(2024, 3, 1, 9, 30, 15, tzinfo=UTC)
    assert reviewed_at_text(moment) == "2024-03-01T09:30:15Z"
    with pytest.raises(AnalysisInputError):
        reviewed_at_text(datetime(2024, 3, 1))  # naive timestamps are rejected


def comparison() -> MonthComparison:
    reviews = run()
    return reviews[1].comparison  # type: ignore[return-value]


def test_comparison_carries_sample_sizes_always() -> None:
    item = comparison()
    assert item is not None
    assert item.month == "2024-02"
    assert item.previous_month == "2024-01"
    assert item.current_finalized == 1 and item.previous_finalized == 4
    assert item.both_sufficient is False  # 1 and 4 finalized < default minimum 10
    assert item.win_rate_delta is None
    assert item.average_final_return_delta is None


def test_comparison_deltas_exist_only_for_sufficient_samples() -> None:
    item = comparison()
    with pytest.raises(AnalysisInputError):
        replace(item, win_rate_delta=Decimal(1))
    sufficient = replace(
        item,
        current_finalized=10,
        previous_finalized=10,
        both_sufficient=True,
        win_rate_delta=Decimal(0),
        average_final_return_delta=Decimal("0.1"),
    )
    assert sufficient.both_sufficient is True
    with pytest.raises(AnalysisInputError):
        replace(sufficient, average_final_return_delta=None)


def test_comparison_validates_months_and_counts() -> None:
    item = comparison()
    with pytest.raises(AnalysisInputError):
        replace(item, previous_month="2024-03")  # not before the reviewed month
    with pytest.raises(AnalysisInputError):
        replace(item, current_finalized=-1)
    with pytest.raises(AnalysisInputError):
        replace(item, month="2024-2")
    with pytest.raises(AnalysisInputError):
        replace(item, both_sufficient=1)  # type: ignore[arg-type]


def first_review() -> MonthlyReview:
    return run()[0]


def test_review_pairs_the_month_bucket_exactly() -> None:
    review = first_review()
    assert review.month == "2024-01"
    assert review.bucket.group == "month" and review.bucket.name == "2024-01"
    assert review.previous_bucket is None and review.comparison is None
    assert review.review_id.startswith("monthly-review:")


def test_review_validates_bucket_and_comparison_pairing() -> None:
    review = run()[1]
    with pytest.raises(AnalysisInputError):
        replace(review, bucket=replace(review.bucket, group="symbol"))
    with pytest.raises(AnalysisInputError):
        replace(review, bucket=replace(review.bucket, name="2024-03"))
    with pytest.raises(AnalysisInputError):
        replace(review, comparison=None)  # previous bucket still present
    with pytest.raises(AnalysisInputError):
        replace(review, previous_bucket=None)  # comparison still present
    with pytest.raises(AnalysisInputError):
        replace(review, comparison=replace(review.comparison, month="2024-03"))
    with pytest.raises(AnalysisInputError):
        replace(
            review,
            comparison=replace(review.comparison, current_finalized=99),
        )


def test_review_uses_the_configured_comparison_minimum() -> None:
    review = run()[1]
    with pytest.raises(AnalysisInputError):
        replace(review, settings=ReviewConfig(minimum_finalized_for_comparison=1))
    relaxed = run(config=ReviewConfig(minimum_finalized_for_comparison=1))[1]
    assert relaxed.comparison is not None
    assert relaxed.comparison.both_sufficient is True  # 1 and 4 finalized >= 1
    assert relaxed.comparison.win_rate_delta == Decimal(0)
    assert relaxed.comparison.average_final_return_delta is not None


def test_review_validates_identity_text() -> None:
    review = first_review()
    with pytest.raises(AnalysisInputError):
        replace(review, review_id="review:abc")
    with pytest.raises(AnalysisInputError):
        replace(review, review_id=" monthly-review:x ")
