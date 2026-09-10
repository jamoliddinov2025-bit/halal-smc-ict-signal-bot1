"""Immutable monthly-review facts over one performance report. Not advice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.performance.models import PerformanceBucket
from smcsignal.analysis.review.config import ReviewConfig


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _month(name: str, label: str = "month") -> None:
    _text(name, label)
    if (
        len(name) != 7
        or name[4] != "-"
        or not name[:4].isdigit()
        or not name[5:].isdigit()
        or not 1 <= int(name[5:]) <= 12
    ):
        raise AnalysisInputError(f"{label} must use the YYYY-MM UTC calendar form")


def previous_month(month: str) -> str:
    """The UTC calendar month immediately before the given month."""

    _month(month)
    year, number = int(month[:4]), int(month[5:])
    if number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{number - 1:02d}"


@dataclass(frozen=True, slots=True)
class MonthComparison:
    """Descriptive month-over-month deltas. Quoted only for sufficient samples.

    both_sufficient is true exactly when both months reach the configured
    minimum finalized count. Sample sizes are carried regardless so readers
    can always see how much evidence stands behind each month.
    """

    month: str
    previous_month: str
    current_finalized: int
    previous_finalized: int
    both_sufficient: bool
    win_rate_delta: Decimal | None
    average_final_return_delta: Decimal | None

    def __post_init__(self) -> None:
        _month(self.month)
        _month(self.previous_month, "previous_month")
        if self.previous_month >= self.month:
            raise AnalysisInputError("the previous month precedes the reviewed month")
        for name, value in (
            ("current_finalized", self.current_finalized),
            ("previous_finalized", self.previous_finalized),
        ):
            if type(value) is not int or value < 0:
                raise AnalysisInputError(f"{name} must be a nonnegative integer")
        if type(self.both_sufficient) is not bool:
            raise AnalysisInputError("both_sufficient must be a boolean")
        deltas = (self.win_rate_delta, self.average_final_return_delta)
        if self.both_sufficient:
            for delta in deltas:
                if not isinstance(delta, Decimal) or not delta.is_finite():
                    raise AnalysisInputError("sufficient comparisons carry exact Decimal deltas")
        elif any(delta is not None for delta in deltas):
            raise AnalysisInputError(
                "deltas are quoted only when both months are sufficiently sampled"
            )


@dataclass(frozen=True, slots=True)
class MonthlyReview:
    """One UTC calendar month of descriptive signal statistics.

    The month bucket is copied from the consumed Phase 19c report; nothing is
    recomputed or reclassified. A comparison exists only when the report also
    covers a chronologically earlier month, and its deltas exist only when
    both months are sufficiently sampled. Sample sizes are always present.
    """

    settings: ReviewConfig
    month: str
    bucket: PerformanceBucket
    previous_bucket: PerformanceBucket | None
    comparison: MonthComparison | None
    review_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.settings, ReviewConfig):
            raise AnalysisInputError("monthly review requires ReviewConfig")
        _month(self.month)
        if not isinstance(self.bucket, PerformanceBucket):
            raise AnalysisInputError("monthly review requires a PerformanceBucket")
        if self.bucket.group != "month" or self.bucket.name != self.month:
            raise AnalysisInputError("the reviewed bucket is this month's bucket")
        if self.previous_bucket is not None:
            if not isinstance(self.previous_bucket, PerformanceBucket):
                raise AnalysisInputError("the previous bucket must be a PerformanceBucket")
            if self.previous_bucket.group != "month" or self.previous_bucket.name >= self.month:
                raise AnalysisInputError("the previous bucket precedes this month")
        if (self.comparison is None) is not (self.previous_bucket is None):
            raise AnalysisInputError("a comparison exists exactly with a previous month")
        if self.comparison is not None:
            if self.comparison.month != self.month:
                raise AnalysisInputError("the comparison reviews this month")
            if self.comparison.previous_month != self.previous_bucket.name:  # type: ignore[union-attr]
                raise AnalysisInputError("the comparison names the previous bucket's month")
            if self.comparison.current_finalized != self.bucket.finalized_count:
                raise AnalysisInputError("the comparison carries this month's sample size")
            if self.comparison.previous_finalized != self.previous_bucket.finalized_count:  # type: ignore[union-attr]
                raise AnalysisInputError("the comparison carries the previous month's sample size")
            minimum = self.settings.minimum_finalized_for_comparison
            expected = (
                self.bucket.finalized_count >= minimum
                and self.previous_bucket.finalized_count >= minimum  # type: ignore[union-attr]
            )
            if self.comparison.both_sufficient is not expected:
                raise AnalysisInputError(
                    "both_sufficient must match the configured comparison minimum"
                )
        _text(self.review_id, "review_id")
        if not self.review_id.startswith("monthly-review:"):
            raise AnalysisInputError("review ids use the monthly-review prefix")

    def month_totals_line(self) -> str:
        """One-line sample-size summary used by review text."""

        bucket = self.bucket
        return (
            f"signals {bucket.total_buy_signals}, open {bucket.open_count}, "
            f"finalized {bucket.finalized_count} "
            f"(win {bucket.win_count}, loss {bucket.loss_count}, flat {bucket.flat_count})"
        )

    def comparison_line(self) -> str:
        """The comparison sentence; sample sizes are always spelled out."""

        if self.comparison is None:
            return "comparison: none (first reviewed month)"
        comparison = self.comparison
        if not comparison.both_sufficient:
            return (
                f"comparison with {comparison.previous_month}: suppressed "
                f"(samples {comparison.current_finalized} and "
                f"{comparison.previous_finalized} finalized; minimum "
                f"{self.settings.minimum_finalized_for_comparison})"
            )
        win_rate_delta = comparison.win_rate_delta
        average_delta = comparison.average_final_return_delta
        if win_rate_delta is None or average_delta is None:
            raise AnalysisInputError("sufficient comparisons carry exact deltas")
        return (
            f"comparison with {comparison.previous_month}: "
            f"win_rate delta {win_rate_delta}, "
            f"average_final_return delta {average_delta} "
            f"(samples {comparison.current_finalized} and "
            f"{comparison.previous_finalized} finalized)"
        )


def reviewed_at_text(moment: datetime) -> str:
    """UTC instant as the fixed review-text timestamp form."""

    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise AnalysisInputError("review timestamps are timezone-aware UTC instants")
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
