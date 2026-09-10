"""Phase 22 deterministic ranking invariants."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.intelligence import analyze_report
from tests.intelligence.helpers import intelligence, report

EASY = intelligence(minimum_finalized_for_diagnosis=1, minimum_finalized_for_ranking=1)


def test_ranks_are_contiguous_and_only_eligible_cells_rank() -> None:
    analyzed = analyze_report(report(), EASY)
    for cells in (analyzed.by_setup, analyzed.by_symbol, analyzed.by_regime):
        ranked = [cell for cell in cells if cell.rank is not None]
        assert sorted(cell.rank for cell in ranked) == list(range(1, len(ranked) + 1))
        for cell in cells:
            assert (cell.rank is not None) == (cell.finalized_count >= 1)


def test_best_rank_has_the_highest_average() -> None:
    analyzed = analyze_report(report(), EASY)
    ranked = [cell for cell in analyzed.by_setup if cell.rank is not None]
    averages = {cell.name: cell.average_final_return for cell in ranked}
    assert all(value is not None for value in averages.values())
    best = min(ranked, key=lambda cell: cell.rank if cell.rank is not None else 0)
    assert best.average_final_return == max(averages.values())


def test_comparator_is_descending_average_then_name() -> None:
    analyzed = analyze_report(report(), EASY)
    ranked = [cell for cell in analyzed.by_setup if cell.rank is not None]
    by_name = {cell.name: cell.rank for cell in ranked}
    # The comparator: descending average final return, then descending win
    # rate, then ascending name. Cells remain name-sorted in the report; the
    # .rank field carries the deterministic ordering.
    comparator = {
        cell.name: (
            -(cell.average_final_return if cell.average_final_return is not None else Decimal(0)),
            -(cell.win_rate if cell.win_rate is not None else Decimal(0)),
            cell.name,
        )
        for cell in ranked
    }
    # best (rank 1) has the highest average final return
    top = max(ranked, key=lambda cell: cell.average_final_return or Decimal(0))
    assert top.rank == 1
    # every rank matches the position this comparator would assign
    for position, (name, _key) in enumerate(
        sorted(comparator.items(), key=lambda item: item[1]), start=1
    ):
        assert by_name[name] == position


def test_high_diagnosis_minimum_gates_all_labels() -> None:
    analyzed = analyze_report(
        report(),
        intelligence(minimum_finalized_for_diagnosis=100, minimum_finalized_for_ranking=100),
    )
    for cell in analyzed.by_setup:
        assert cell.rank is None
        assert cell.pattern.value == "UNDERSAMPLED"
