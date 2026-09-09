"""Reuse existing Phase 19c fixtures; reviews recompute nothing."""

from __future__ import annotations

from smcsignal.analysis.performance import PerformanceConfig, analyze_performance
from smcsignal.analysis.review import (
    ReviewConfig,
    build_monthly_reviews,
    render_review_text,
)
from tests.performance.helpers import (
    february_replay,
    january_long_replay,
    rising_replay,
)


def two_month_report(minimum: int | None = None):
    jan_out, jan_attr = january_long_replay()
    feb_out, feb_attr = february_replay()
    config = (
        PerformanceConfig()
        if minimum is None
        else PerformanceConfig(minimum_finalized_for_ranking=minimum)
    )
    return analyze_performance(
        {"jan": jan_out, "feb": feb_out}, {"jan": jan_attr, "feb": feb_attr}, config
    )


def single_month_report():
    outcomes, attributions = rising_replay()
    return analyze_performance({"btc": outcomes}, {"btc": attributions})


def zero_signal_report():
    from smcsignal.analysis.outcome_tracking import (
        OutcomeTrackingConfig,
        analyze_outcome_tracking,
    )
    from smcsignal.analysis.setup_attribution import analyze_setup_attribution
    from tests.mtf.helpers import EIGHT
    from tests.performance.helpers import candles_at, engine_frames

    frames = engine_frames(candles_at(EIGHT, tuple(range(20, 37))), symbol="XYZUSDT")
    outcomes = analyze_outcome_tracking(frames, OutcomeTrackingConfig())
    attributions = analyze_setup_attribution(frames)
    return analyze_performance({"xyz": outcomes}, {"xyz": attributions})


def run(report=None, config=None):
    return build_monthly_reviews(
        report if report is not None else two_month_report(),
        config if config is not None else ReviewConfig(),
    )


def text(reviews=None, *, generated_at=None):
    from datetime import UTC, datetime

    return render_review_text(
        reviews if reviews is not None else run(),
        generated_at=generated_at if generated_at is not None else datetime(2024, 3, 1, tzinfo=UTC),
    )


__all__ = [
    "run",
    "single_month_report",
    "text",
    "two_month_report",
    "zero_signal_report",
]
