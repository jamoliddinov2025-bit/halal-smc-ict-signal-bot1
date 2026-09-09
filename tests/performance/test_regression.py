from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from smcsignal.analysis import (
    PerformanceBucket,
    PerformanceConfig,
    PerformanceReport,
    analyze_performance,
    load_performance_config,
)
from tests.performance.helpers import rising_replay, run

SRC = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"

# Modules whose decisions must never read performance analytics.
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


def test_report_building_leaves_outcome_frames_unchanged() -> None:
    outcomes, attributions = rising_replay()
    saved = deepcopy(outcomes)
    report = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert outcomes == saved
    assert report.overall.total_buy_signals == 4


def test_outcome_replays_are_untouched_by_performance_consumption() -> None:
    outcomes, attributions = rising_replay()
    analyze_performance({"btc": outcomes}, {"btc": attributions})
    fresh_outcomes, fresh_attributions = rising_replay()
    assert outcomes == fresh_outcomes
    assert attributions == fresh_attributions


def test_no_decision_module_imports_performance_context() -> None:
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for token in ("analysis.performance", "PerformanceReport", "analyze_performance"):
            assert token not in text, f"{path.relative_to(SRC)} must not read performance"


def test_performance_module_imports_no_new_dependencies() -> None:
    from smcsignal.analysis.performance import analyzer as performance_analyzer

    text = Path(performance_analyzer.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib", "http", "socket", "numpy", "pandas"):
        assert forbidden not in text


def test_analysis_package_exports_the_performance_api() -> None:
    import smcsignal.analysis as analysis

    for name in (
        "PerformanceBucket",
        "PerformanceConfig",
        "PerformanceReport",
        "analyze_performance",
        "load_performance_config",
    ):
        assert getattr(analysis, name) is not None
    assert analysis.PerformanceConfig() == PerformanceConfig()
    assert (
        load_performance_config(
            Path(__file__).resolve().parents[2] / "config" / "performance.example.toml"
        )
        == PerformanceConfig()
    )
    assert issubclass(PerformanceBucket, object) and PerformanceReport is not None
    assert run().overall.group == "overall"
