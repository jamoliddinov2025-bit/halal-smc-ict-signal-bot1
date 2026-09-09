"""Phase 19d API: monthly descriptive reviews over one performance report."""

from smcsignal.analysis.review.calculation import (
    build_monthly_reviews,
    review_identity,
)
from smcsignal.analysis.review.config import (
    DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON,
    MAXIMUM_FINALIZED_FOR_COMPARISON,
    METHODOLOGY_VERSION,
    ReviewConfig,
    load_review_config,
)
from smcsignal.analysis.review.models import (
    MonthComparison,
    MonthlyReview,
    previous_month,
    reviewed_at_text,
)
from smcsignal.analysis.review.text import render_review_text

__all__ = [
    "DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON",
    "MAXIMUM_FINALIZED_FOR_COMPARISON",
    "METHODOLOGY_VERSION",
    "MonthComparison",
    "MonthlyReview",
    "ReviewConfig",
    "build_monthly_reviews",
    "load_review_config",
    "previous_month",
    "render_review_text",
    "review_identity",
    "reviewed_at_text",
]
