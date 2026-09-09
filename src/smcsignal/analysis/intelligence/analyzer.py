"""Strategy-intelligence evaluation over the Phase 21 robustness report.

Phase 22 is a terminal consumer: it reads the already-validated Phase 21
``RobustnessReport`` and never re-runs the replay, never re-detects regimes,
and never recomputes outcomes. Its population is exactly the non-overlapping
validation rows Phase 21 produced, so every described signal is counted once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from smcsignal.analysis.backtest.models import BacktestSignalResult
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.intelligence.calculation import build_cell, rank_cells
from smcsignal.analysis.intelligence.config import IntelligenceConfig
from smcsignal.analysis.intelligence.evidence import intelligence_identity
from smcsignal.analysis.intelligence.models import (
    IntelligenceCell,
    IntelligenceDimension,
    IntelligenceReport,
)
from smcsignal.analysis.performance.calculation import month_of
from smcsignal.analysis.robustness.models import (
    MarketRegime,
    RobustnessReport,
)

if TYPE_CHECKING:
    from collections.abc import Callable

UNCLASSIFIED_REGIME = "unclassified"


def _regime_label(regime: MarketRegime | None) -> str:
    """The Phase 21 regime label verbatim, or the unclassified bucket."""
    return UNCLASSIFIED_REGIME if regime is None else regime.value


def _iter_rows(report: RobustnessReport) -> tuple[BacktestSignalResult, ...]:
    """Every validation row across the report's datasets, counted once."""

    rows: list[BacktestSignalResult] = []
    for dataset_result in report.datasets:
        rows.extend(dataset_result.validation_rows)
    return tuple(rows)


def _regime_map(report: RobustnessReport) -> dict[str, str]:
    """signal_id -> regime bucket label, verbatim from the Phase 21 annotations."""

    mapping: dict[str, str] = {}
    for dataset_result in report.datasets:
        for signal_id, regime in dataset_result.row_regimes.items():
            mapping[signal_id] = _regime_label(regime)
    return mapping


def _require_report(report: object) -> None:
    if not isinstance(report, RobustnessReport):
        raise AnalysisInputError("strategy intelligence requires a Phase 21 RobustnessReport")


def _group(
    rows: Sequence[BacktestSignalResult], key: Callable[[BacktestSignalResult], str]
) -> dict[str, list[BacktestSignalResult]]:
    grouped: dict[str, list[BacktestSignalResult]] = {}
    for row in rows:
        grouped.setdefault(key(row), []).append(row)
    return grouped


def _cells(
    grouped: Mapping[str, Sequence[BacktestSignalResult]],
    dimension: IntelligenceDimension,
    config: IntelligenceConfig,
) -> tuple[IntelligenceCell, ...]:
    cells: list[IntelligenceCell] = []
    for name in sorted(grouped):
        cells.append(build_cell(tuple(grouped[name]), dimension, name, config))
    return rank_cells(tuple(cells), config)


def analyze_report(
    report: RobustnessReport, config: IntelligenceConfig | None = None
) -> IntelligenceReport:
    """Build one deterministic strategy-intelligence report from a Phase 21 report.

    Cell membership is decided exclusively by signal-time facts: the Phase 19
    combination key (setup), symbol, timeframe, UTC month of the signal candle,
    and the Phase 21 regime annotation at the signal candle. Post-outcome
    fields appear only as descriptive statistics inside an already-formed cell,
    so future rows or re-annotated tails can never rewrite an existing cell.
    """

    _require_report(report)
    if config is not None and not isinstance(config, IntelligenceConfig):
        raise AnalysisConfigurationError("config must be IntelligenceConfig")
    settings = config if config is not None else IntelligenceConfig()
    rows = _iter_rows(report)
    if not rows:
        raise AnalysisInputError("strategy intelligence requires at least one validation row")
    regimes = _regime_map(report)

    overall = build_cell(rows, IntelligenceDimension.OVERALL, "all", settings)
    by_setup = _cells(
        _group(rows, lambda row: row.combination_key), IntelligenceDimension.SETUP, settings
    )
    by_symbol = _cells(_group(rows, lambda row: row.symbol), IntelligenceDimension.SYMBOL, settings)
    by_timeframe = _cells(
        _group(rows, lambda row: row.timeframe), IntelligenceDimension.TIMEFRAME, settings
    )
    by_month = _cells(
        _group(rows, lambda row: month_of(row.opened_at)), IntelligenceDimension.MONTH, settings
    )
    by_regime = _cells(
        _group(rows, lambda row: regimes[row.signal_id]), IntelligenceDimension.REGIME, settings
    )

    series_keys = report.series_keys
    report_id = intelligence_identity(settings, rows, regimes)
    return IntelligenceReport(
        settings=settings,
        overall=overall,
        by_setup=by_setup,
        by_symbol=by_symbol,
        by_timeframe=by_timeframe,
        by_month=by_month,
        by_regime=by_regime,
        series_keys=tuple(series_keys),
        report_id=report_id,
    )


def run_intelligence(
    report: RobustnessReport, config: IntelligenceConfig | None = None
) -> IntelligenceReport:
    """Alias of ``analyze_report`` over a single Phase 21 report."""
    return analyze_report(report, config)
