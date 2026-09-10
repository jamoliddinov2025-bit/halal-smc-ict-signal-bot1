"""Deterministic plain-text rendering of monthly reviews. No markup, no advice."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.review.config import METHODOLOGY_VERSION
from smcsignal.analysis.review.models import MonthlyReview, reviewed_at_text


def render_review_text(
    reviews: tuple[MonthlyReview, ...],
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render reviews as stable plain text; sample sizes are always shown.

    The text is a deterministic function of the reviews and the UTC generation
    instant. It contains no recommendations, forecasts, or execution language.
    """

    if not isinstance(reviews, tuple) or not reviews:
        raise AnalysisInputError("rendering requires at least one monthly review")
    for review in reviews:
        if not isinstance(review, MonthlyReview):
            raise AnalysisInputError("rendering requires MonthlyReview records")
    moment = generated_at if generated_at is not None else datetime.now(UTC)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise AnalysisInputError("generated_at must be a timezone-aware UTC instant")
    lines = [
        f"SMC/ICT signal bot monthly review ({METHODOLOGY_VERSION})",
        "descriptive statistics of published signal records; not advice",
        f"generated {reviewed_at_text(moment)}",
        "",
    ]
    for review in reviews:
        bucket = review.bucket
        lines.append(f"month {review.month}: {review.month_totals_line()}")
        lines.append(f"  win_rate {_optional(bucket.win_rate)}")
        lines.append(f"  average_final_return {_optional(bucket.average_final_return)}")
        lines.append(f"  average_mfe_return {_optional(bucket.average_mfe_return)}")
        lines.append(f"  average_mae_return {_optional(bucket.average_mae_return)}")
        lines.append(f"  {review.comparison_line()}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _optional(value: Decimal | None) -> str:
    return "undefined" if value is None else str(value)
