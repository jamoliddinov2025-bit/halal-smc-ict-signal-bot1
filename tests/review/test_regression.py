from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from smcsignal.analysis import (
    MonthComparison,
    MonthlyReview,
    ReviewConfig,
    build_monthly_reviews,
    load_review_config,
    render_review_text,
)
from tests.review.helpers import run, two_month_report

SRC = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"

# Modules whose decisions must never read review text or reviews.
DECISION_PACKAGES = (
    "structure.py",
    "swings.py",
    "trend.py",
    "liquidity",
    "displacement",
    "fvg",
    "order_blocks",
    "premium_discount",
    "mss",
    "breaker_blocks",
    "mitigation_blocks",
    "ote",
    "mtf",
    "halal_filter",
    "setup_quality",
    "signal_eligibility",
    "signal_engine",
    "outcome_tracking",
    "indicators",
    "setup_attribution",
    "performance",
    "config.py",
    "models.py",
    "errors.py",
    "provenance.py",
)


def _sources() -> list[Path]:
    files: list[Path] = []
    for name in DECISION_PACKAGES:
        path = SRC / name
        if path.is_dir():
            files.extend(sorted(path.glob("*.py")))
        elif path.exists():
            files.append(path)
    return files


def test_review_building_leaves_the_report_unchanged() -> None:
    report = two_month_report()
    saved = deepcopy(report)
    reviews = build_monthly_reviews(report)
    assert report == saved
    assert [review.month for review in reviews] == ["2024-01", "2024-02"]


def test_no_decision_module_imports_review_context() -> None:
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for token in ("analysis.review", "MonthlyReview", "render_review_text"):
            assert token not in text, f"{path.relative_to(SRC)} must not read reviews"


def test_review_module_imports_no_new_dependencies() -> None:
    from smcsignal.analysis.review import calculation as review_calculation

    text = Path(review_calculation.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib", "http", "socket", "numpy", "pandas"):
        assert forbidden not in text


def test_analysis_package_exports_the_review_api() -> None:
    import smcsignal.analysis as analysis

    for name in (
        "MonthComparison",
        "MonthlyReview",
        "ReviewConfig",
        "build_monthly_reviews",
        "load_review_config",
        "render_review_text",
    ):
        assert getattr(analysis, name) is not None
    assert analysis.ReviewConfig() == ReviewConfig()
    assert (
        load_review_config(Path(__file__).resolve().parents[2] / "config" / "review.example.toml")
        == ReviewConfig()
    )
    reviews = run()
    assert all(isinstance(review, MonthlyReview) for review in reviews)
    assert isinstance(reviews[1].comparison, MonthComparison)
    assert "not advice" in render_review_text(reviews)[:200]
