from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from smcsignal.analysis.performance import analyze_performance
from tests.performance.helpers import rising_replay


def test_performance_reads_only_published_records() -> None:
    """The layer consumes outcome and attribution records, never signal frames."""

    package = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"
    for module in sorted((package / "performance").glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "signal_engine" not in text, f"{module.name} must not read signal frames"
        assert "SignalSnapshot" not in text, f"{module.name} must not read signal frames"
        assert "classify_outcome" not in text, f"{module.name} must not reclassify"


def test_report_building_never_mutates_its_inputs() -> None:
    outcomes, attributions = rising_replay()
    saved_outcomes = deepcopy(outcomes)
    saved_attributions = deepcopy(attributions)
    analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert outcomes == saved_outcomes
    assert attributions == saved_attributions


def test_reports_are_deterministic_across_repeated_builds() -> None:
    outcomes, attributions = rising_replay()
    first = analyze_performance({"btc": outcomes}, {"btc": attributions})
    second = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert first == second
    assert deepcopy(first) == first


def test_prefix_reports_show_only_what_was_published() -> None:
    """A cut replay cannot report finalizations the future has not published."""

    outcomes, attributions = rising_replay()
    early = analyze_performance({"btc": outcomes[:15]}, {"btc": attributions[:15]})
    assert early.overall.finalized_count == 1  # the index-4 WIN closes at candle 14
    shorter = analyze_performance({"btc": outcomes[:14]}, {"btc": attributions[:14]})
    assert shorter.overall.finalized_count == 0
    assert shorter.overall.win_rate is None
    # The fourth BUY is not published until candle 16; a cut replay cannot see it.
    assert shorter.overall.total_buy_signals == 3
    assert shorter.overall.open_count == 3


def test_report_equality_and_hash_follow_content() -> None:
    outcomes, attributions = rising_replay()
    first = analyze_performance({"btc": outcomes}, {"btc": attributions})
    second = analyze_performance({"btc": outcomes}, {"btc": attributions})
    assert first == second
    assert len({first, second}) == 1
    other = analyze_performance({"btc": outcomes})  # no attribution profiles
    assert other != first
    assert other.attribution_settings is None
