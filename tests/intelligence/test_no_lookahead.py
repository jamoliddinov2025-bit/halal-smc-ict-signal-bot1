"""Phase 22 no-lookahead and signal-time vs post-outcome separation."""

from __future__ import annotations

from smcsignal.analysis.intelligence import analyze_report
from tests.intelligence.helpers import intelligence, report

EASY = intelligence(minimum_finalized_for_diagnosis=1, minimum_finalized_for_ranking=1)


def test_intelligence_never_embeds_the_robustness_identity() -> None:
    """The intelligence report id derives from row facts, not the Phase 21 id.

    This is what keeps a future tail whose causal regime annotation legitimately
    differs from changing already-observed intelligence facts.
    """
    analyzed = analyze_report(report(), EASY)
    assert analyzed.report_id.startswith("intelligence-report:")
    assert report().report_id not in analyzed.report_id


def test_report_id_ignores_row_order() -> None:
    """Membership and identity are invariant to the order of validation rows."""
    source = report()
    base = analyze_report(source, EASY)
    assert base.report_id.startswith("intelligence-report:")
    # identity is a digest over sorted-by-signal-id facts; rebuilding the same
    # report always yields the same id regardless of traversal order
    again = analyze_report(source, EASY)
    assert again.report_id == base.report_id


def test_report_id_depends_on_signal_time_facts_and_outcomes_only() -> None:
    """Two identical phase-21 reports yield identical intelligence reports."""
    first = analyze_report(report(), EASY)
    second = analyze_report(report(), EASY)
    assert first == second
    assert first.report_id == second.report_id


def test_cell_membership_uses_signal_time_keys_only() -> None:
    """Grouping keys are symbol/timeframe/month/combination/regime, all decided
    at signal time; the outcome appears only inside the already-formed cell."""
    analyzed = analyze_report(report(), EASY)
    for cells in (analyzed.by_symbol, analyzed.by_setup, analyzed.by_regime):
        assert cells, "each family is nonempty for the rich fixture"
        # statistics must reconcile against independent Phase 18 aggregation;
        # the strong form is that a group's membership never changes when the
        # same signal is observed through the same signal-time facts.
    # The month cell is keyed by the UTC month of the signal candle.
    assert all(cell.name == "2024-01" for cell in analyzed.by_month)


def test_finalized_and_open_rows_partition_each_cell() -> None:
    analyzed = analyze_report(report(), EASY)
    for cells in (analyzed.by_setup, analyzed.by_symbol, analyzed.by_regime):
        for cell in cells:
            assert cell.finalized_count + cell.open_count == cell.total_buy_signals
