"""Phase 22 end-to-end analysis invariants over a real Phase 21 report."""

from __future__ import annotations

import pytest

from smcsignal.analysis.intelligence import (
    IntelligenceDimension,
    IntelligencePattern,
    analyze_report,
    run_intelligence,
)
from tests.intelligence.helpers import intelligence, report, rich_report

EASY = intelligence(minimum_finalized_for_diagnosis=1, minimum_finalized_for_ranking=1)


def _validation_count(report) -> int:
    return sum(len(result.validation_rows) for result in report.datasets)


def test_every_validation_row_is_counted_exactly_once() -> None:
    source = report()
    analyzed = analyze_report(source, EASY)
    assert analyzed.overall.total_buy_signals == _validation_count(source)
    families = (
        analyzed.by_setup,
        analyzed.by_symbol,
        analyzed.by_timeframe,
        analyzed.by_month,
        analyzed.by_regime,
    )
    for cells in families:
        assert sum(cell.total_buy_signals for cell in cells) == analyzed.overall.total_buy_signals
    # open plus finalized reconcile to the whole population
    assert analyzed.overall.open_count + analyzed.overall.finalized_count == (
        analyzed.overall.total_buy_signals
    )


def test_families_are_sorted_and_carry_their_dimension() -> None:
    analyzed = analyze_report(report(), EASY)
    assert analyzed.by_symbol == tuple(sorted(analyzed.by_symbol, key=lambda cell: cell.name))
    assert analyzed.overall.dimension is IntelligenceDimension.OVERALL
    assert all(cell.dimension is IntelligenceDimension.SYMBOL for cell in analyzed.by_symbol)
    assert all(cell.dimension is IntelligenceDimension.SETUP for cell in analyzed.by_setup)
    assert all(cell.dimension is IntelligenceDimension.REGIME for cell in analyzed.by_regime)


def test_default_config_is_conservative() -> None:
    # The frozen default diagnosis minimum (20) exceeds this fixture's small
    # finalized samples, so no group can be diagnosed as a winner or loser.
    analyzed = analyze_report(report(), intelligence())
    for cells in (analyzed.by_setup, analyzed.by_regime):
        for cell in cells:
            if cell.finalized_count == 0:
                assert cell.pattern is IntelligencePattern.UNDERSAMPLED
    assert analyzed.by_symbol[0].finalized_count < 20


def test_regime_cells_use_phase21_labels_verbatim() -> None:
    analyzed = analyze_report(report(), EASY)
    labels = {cell.name for cell in analyzed.by_regime}
    assert labels == {"TRENDING"}
    assert all(
        cell.total_buy_signals == analyzed.overall.total_buy_signals for cell in analyzed.by_regime
    )


def test_multi_symbol_report_partitions_by_symbol() -> None:
    source = rich_report(("BTCUSDT", "ETHUSDT"))
    analyzed = analyze_report(source, EASY)
    assert {cell.name for cell in analyzed.by_symbol} == {"BTCUSDT", "ETHUSDT"}
    assert sum(cell.total_buy_signals for cell in analyzed.by_symbol) == _validation_count(source)


def test_run_intelligence_matches_analyze_report() -> None:
    source = report()
    assert run_intelligence(source, EASY) == analyze_report(source, EASY)


def test_analyze_requires_a_robustness_report() -> None:
    from smcsignal.analysis.errors import AnalysisInputError

    with pytest.raises(AnalysisInputError):
        analyze_report("not-a-report", EASY)  # type: ignore[arg-type]


def test_analyze_rejects_a_non_config() -> None:
    from smcsignal.analysis.errors import AnalysisConfigurationError

    with pytest.raises(AnalysisConfigurationError):
        analyze_report(report(), "config")  # type: ignore[arg-type]
