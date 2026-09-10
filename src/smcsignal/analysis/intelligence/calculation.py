"""Exact descriptive intelligence arithmetic over Phase 21 validation facts.

The Phase 18 ``aggregate`` helper is reused unchanged over the SignalOutcome
objects the Phase 21 validation rows already carry, so every count, sum, and
50-significant-digit ratio matches Phase 18/19 exactly. Membership is decided
upstream by signal-time keys; this module only labels an already-formed group.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from smcsignal.analysis.backtest.models import BacktestSignalResult
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.intelligence.config import IntelligenceConfig
from smcsignal.analysis.intelligence.models import (
    DiagnosticLabel,
    IntelligenceCell,
    IntelligenceDimension,
    IntelligencePattern,
)
from smcsignal.analysis.outcome_tracking.calculation import aggregate
from smcsignal.analysis.outcome_tracking.models import AnalyticsSummary


def _finalized_rows(rows: tuple[BacktestSignalResult, ...]) -> tuple[BacktestSignalResult, ...]:
    if not rows:
        raise AnalysisInputError("a cell requires at least one signal row")
    if not all(isinstance(row, BacktestSignalResult) for row in rows):
        raise AnalysisInputError("a cell requires BacktestSignalResult rows")
    return tuple(row for row in rows if row.finalized)


def diagnose(
    summary: AnalyticsSummary, config: IntelligenceConfig
) -> tuple[bool, IntelligencePattern, DiagnosticLabel]:
    """Determine sufficiency, pattern, and diagnostic from an exact summary.

    A group is ``sufficient`` when its finalized sample reaches the configured
    diagnosis minimum. Sufficient groups are classified purely descriptively: a
    WINNER reaches the win-rate floor with a positive average final return, a
    LOSER stays at or below the win-rate ceiling with a negative average final
    return, and everything else is NEUTRAL. The pattern is a research label and
    never influences any later computation or signal.
    """

    if not isinstance(summary, AnalyticsSummary):
        raise AnalysisInputError("diagnosis requires an AnalyticsSummary")
    if not isinstance(config, IntelligenceConfig):
        raise AnalysisInputError("diagnosis requires IntelligenceConfig")
    sufficient = summary.finalized_count >= config.minimum_finalized_for_diagnosis
    if not sufficient:
        return False, IntelligencePattern.UNDERSAMPLED, DiagnosticLabel.UNDETERMINED
    win_rate = summary.win_rate
    average = summary.average_final_return
    assert win_rate is not None and average is not None  # a sufficient cell is finalized
    if win_rate >= config.winner_win_rate_floor and average > 0:
        return True, IntelligencePattern.WINNER, DiagnosticLabel.STRENGTH
    if win_rate <= config.loser_win_rate_ceiling and average < 0:
        return True, IntelligencePattern.LOSER, DiagnosticLabel.WEAKNESS
    return True, IntelligencePattern.NEUTRAL, DiagnosticLabel.UNDETERMINED


def build_cell(
    rows: tuple[BacktestSignalResult, ...],
    dimension: IntelligenceDimension,
    name: str,
    config: IntelligenceConfig,
) -> IntelligenceCell:
    """One unranked descriptive cell over a group's published rows.

    The finalized subset is aggregated with the Phase 18 helper; open outcomes
    contribute to the open count only. ``dimension``, ``name``, and the rows'
    membership are chosen upstream by signal-time facts; this never revisits
    those facts or any post-outcome classification.
    """

    if not isinstance(config, IntelligenceConfig):
        raise AnalysisInputError("cells require IntelligenceConfig")
    if not isinstance(dimension, IntelligenceDimension):
        raise AnalysisInputError("cells require an IntelligenceDimension")
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise AnalysisInputError("cell names must be nonempty, trimmed strings")
    if not isinstance(rows, tuple) or not rows:
        raise AnalysisInputError("a cell requires at least one signal row")
    if not all(isinstance(row, BacktestSignalResult) for row in rows):
        raise AnalysisInputError("a cell requires BacktestSignalResult rows")
    finalized_rows = _finalized_rows(rows)
    records = tuple(row.outcome for row in finalized_rows)
    open_count = len(rows) - len(finalized_rows)
    summary = aggregate(records, total_buy_signals=len(rows), open_count=open_count)
    sufficient, pattern, diagnostic = diagnose(summary, config)
    return IntelligenceCell(
        dimension=dimension,
        name=name,
        total_buy_signals=summary.total_buy_signals,
        open_count=summary.open_count,
        win_count=summary.win_count,
        loss_count=summary.loss_count,
        flat_count=summary.flat_count,
        finalized_count=summary.finalized_count,
        final_return_sum=summary.final_return_sum,
        mfe_return_sum=summary.mfe_return_sum,
        mae_return_sum=summary.mae_return_sum,
        win_rate=summary.win_rate,
        average_final_return=summary.average_final_return,
        average_mfe_return=summary.average_mfe_return,
        average_mae_return=summary.average_mae_return,
        sufficient=sufficient,
        pattern=pattern,
        diagnostic=diagnostic,
        rank=None,
    )


def _average(cell: IntelligenceCell) -> Decimal:
    average = cell.average_final_return
    if average is None:
        raise AnalysisInputError("a ranking-eligible cell always has finalized outcomes")
    return average


def _win_rate(cell: IntelligenceCell) -> Decimal:
    rate = cell.win_rate
    if rate is None:
        raise AnalysisInputError("a ranking-eligible cell always has finalized outcomes")
    return rate


def rank_cells(
    cells: tuple[IntelligenceCell, ...], config: IntelligenceConfig
) -> tuple[IntelligenceCell, ...]:
    """Assign deterministic 1-based ranks among ranking-eligible cells.

    Eligible cells (finalized sample at or above the ranking minimum) are
    ordered by descending average final return, then descending win rate, then
    ascending name, so ties resolve deterministically. This is a descriptive
    ordering for research reading only; it selects, enables, disables, vetoes,
    or recommends nothing.
    """

    if not isinstance(config, IntelligenceConfig):
        raise AnalysisInputError("ranking requires IntelligenceConfig")
    if not isinstance(cells, tuple) or not all(
        isinstance(cell, IntelligenceCell) for cell in cells
    ):
        raise AnalysisInputError("ranking requires a tuple of cells")
    eligible = [
        cell for cell in cells if cell.finalized_count >= config.minimum_finalized_for_ranking
    ]
    ordered = sorted(
        eligible,
        key=lambda cell: (-_average(cell), -_win_rate(cell), cell.name),
    )
    rank_by_name = {cell.name: position for position, cell in enumerate(ordered, start=1)}
    return tuple(
        replace(cell, rank=rank_by_name.get(cell.name)) if cell.name in rank_by_name else cell
        for cell in cells
    )
