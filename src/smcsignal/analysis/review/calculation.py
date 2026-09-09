"""Build monthly reviews from one performance report. No recomputation."""

from __future__ import annotations

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.performance.models import PerformanceBucket, PerformanceReport
from smcsignal.analysis.review.config import (
    METHODOLOGY_VERSION,
    ReviewConfig,
)
from smcsignal.analysis.review.models import MonthComparison, MonthlyReview


def _comparison(
    settings: ReviewConfig,
    bucket: PerformanceBucket,
    previous: PerformanceBucket,
) -> MonthComparison:
    minimum = settings.minimum_finalized_for_comparison
    both = bucket.finalized_count >= minimum and previous.finalized_count >= minimum
    win_rate_delta = None
    average_delta = None
    if both:
        current_rate = bucket.win_rate
        previous_rate = previous.win_rate
        current_average = bucket.average_final_return
        previous_average = previous.average_final_return
        if current_rate is None or previous_rate is None:
            raise AnalysisInputError("sufficient months always define win rates")
        if current_average is None or previous_average is None:
            raise AnalysisInputError("sufficient months always define averages")
        win_rate_delta = current_rate - previous_rate
        average_delta = current_average - previous_average
    return MonthComparison(
        month=bucket.name,
        previous_month=previous.name,
        current_finalized=bucket.finalized_count,
        previous_finalized=previous.finalized_count,
        both_sufficient=both,
        win_rate_delta=win_rate_delta,
        average_final_return_delta=average_delta,
    )


def review_identity(settings: ReviewConfig, payload: object) -> str:
    """Stable identity over the configuration and the consumed report facts."""

    from smcsignal.analysis.liquidity.evidence import digest

    identity = digest(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": settings,
            "facts": payload,
        }
    )
    return f"monthly-review:{identity}"


def build_monthly_reviews(
    report: PerformanceReport, config: ReviewConfig | None = None
) -> tuple[MonthlyReview, ...]:
    """One review per UTC month of the report, in chronological order.

    Each review pairs its month bucket with the chronologically previous month
    bucket of the same report. Deltas appear only when both months reach the
    configured minimum finalized count; sample sizes always travel along.
    """

    if not isinstance(report, PerformanceReport):
        raise AnalysisInputError("monthly reviews consume a PerformanceReport")
    if config is not None and not isinstance(config, ReviewConfig):
        raise AnalysisInputError("config must be ReviewConfig")
    settings = config if config is not None else ReviewConfig()
    if not report.by_month:
        raise AnalysisInputError("a report without months cannot be reviewed")
    months = [bucket.name for bucket in report.by_month]
    if months != sorted(months) or len(set(months)) != len(months):
        raise AnalysisInputError("report months must be sorted and unique")
    for name in months:
        if len(name) != 7:
            raise AnalysisInputError("report months use the YYYY-MM form")
    reviews: list[MonthlyReview] = []
    for position, bucket in enumerate(report.by_month):
        previous = report.by_month[position - 1] if position else None
        comparison = _comparison(settings, bucket, previous) if previous is not None else None
        reviews.append(
            MonthlyReview(
                settings=settings,
                month=bucket.name,
                bucket=bucket,
                previous_bucket=previous,
                comparison=comparison,
                review_id=review_identity(
                    settings,
                    {
                        "month": bucket.name,
                        "report_id": report.report_id,
                        "bucket": bucket,
                        "previous": None if previous is None else previous.name,
                    },
                ),
            )
        )
    return tuple(reviews)
