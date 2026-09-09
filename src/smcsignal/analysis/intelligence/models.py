"""Immutable descriptive strategy-intelligence facts. Observational research only.

Every value is copied or recomputed with the existing Phase 18 aggregate
helpers over the published, finalized BUY_SIGNAL outcomes carried by the Phase
21 validation rows. Cells are exact Decimal statistics labelled by a
deterministic winner/loser pattern and a strength/weakness diagnostic whenever
the configured minimum finalized sample is reached. Cell membership is decided
exclusively by signal-time facts (setup combination, symbol, timeframe, UTC
month of the signal candle, Phase 21 regime annotation); post-outcome fields
appear only as statistics inside an already-formed cell. Nothing here is
fitted, optimized, selected, or advised.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.intelligence.config import IntelligenceConfig


class IntelligenceDimension(StrEnum):
    """The descriptive grouping dimension of one intelligence cell."""

    OVERALL = "overall"
    SETUP = "setup"
    SYMBOL = "symbol"
    TIMEFRAME = "timeframe"
    MONTH = "month"
    REGIME = "regime"


class IntelligencePattern(StrEnum):
    """Deterministic descriptive winner/loser/undersampled labels."""

    UNDERSAMPLED = "UNDERSAMPLED"
    WINNER = "WINNER"
    LOSER = "LOSER"
    NEUTRAL = "NEUTRAL"


class DiagnosticLabel(StrEnum):
    """Deterministic descriptive strength/weakness readings."""

    UNDETERMINED = "UNDETERMINED"
    STRENGTH = "STRENGTH"
    WEAKNESS = "WEAKNESS"


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _rank_eligible(cell: IntelligenceCell, settings: IntelligenceConfig) -> bool:
    return cell.finalized_count >= settings.minimum_finalized_for_ranking


@dataclass(frozen=True, slots=True)
class IntelligenceCell:
    """Descriptive statistics of one strategy-intelligence group.

    Counts are exact integers and return sums are exact Decimal additions of
    the descriptive per-outcome ratios produced by the Phase 18 aggregate
    helper; rates and averages are undefined (None) whenever no outcome is
    finalized. ``sufficient`` compares the finalized sample against the
    configured diagnosis minimum; it never hides the raw counts (open outcomes
    contribute only to the open count).

    ``pattern`` is the deterministic descriptive winner/loser classification
    (UNDERSAMPLED below the diagnosis minimum, otherwise WINNER, LOSER, or
    NEUTRAL), ``diagnostic`` its strength/weakness reading (STRENGTH for a
    winner, WEAKNESS for a loser, UNDETERMINED otherwise), and ``rank`` its
    position (1 is best) within the same dimension among groups that reach the
    ranking minimum, or None when not ranked. These are research labels over
    published records, not a performance claim, a selection, or a decision.
    """

    dimension: IntelligenceDimension
    name: str
    total_buy_signals: int
    open_count: int
    win_count: int
    loss_count: int
    flat_count: int
    finalized_count: int
    final_return_sum: Decimal
    mfe_return_sum: Decimal
    mae_return_sum: Decimal
    win_rate: Decimal | None
    average_final_return: Decimal | None
    average_mfe_return: Decimal | None
    average_mae_return: Decimal | None
    sufficient: bool
    pattern: IntelligencePattern
    diagnostic: DiagnosticLabel
    rank: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, IntelligenceDimension):
            raise AnalysisInputError("cell dimension must be an IntelligenceDimension")
        _text(self.name, "name")
        counts = (
            self.total_buy_signals,
            self.open_count,
            self.win_count,
            self.loss_count,
            self.flat_count,
            self.finalized_count,
        )
        if any(type(count) is not int or count < 0 for count in counts):
            raise AnalysisInputError("intelligence counts must be nonnegative integers")
        if self.win_count + self.loss_count + self.flat_count != self.finalized_count:
            raise AnalysisInputError("win, loss, and flat counts must sum to finalized_count")
        if self.finalized_count + self.open_count != self.total_buy_signals:
            raise AnalysisInputError("finalized and open counts must sum to total_buy_signals")
        sums = (self.final_return_sum, self.mfe_return_sum, self.mae_return_sum)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in sums):
            raise AnalysisInputError("intelligence sums must be finite Decimals")
        rates = (
            self.win_rate,
            self.average_final_return,
            self.average_mfe_return,
            self.average_mae_return,
        )
        if self.finalized_count == 0:
            if any(value is not None for value in rates):
                raise AnalysisInputError(
                    "rates and averages are undefined without finalized outcomes"
                )
            if self.pattern is not IntelligencePattern.UNDERSAMPLED:
                raise AnalysisInputError("an unfinalized group is reported undersampled")
            if self.diagnostic is not DiagnosticLabel.UNDETERMINED:
                raise AnalysisInputError("an unfinalized group is diagnostically undetermined")
        else:
            for value in rates:
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise AnalysisInputError(
                        "rates and averages must be finite Decimals when finalized exist"
                    )
            win_rate = self.win_rate
            if not isinstance(win_rate, Decimal):
                raise AnalysisInputError("win_rate must be a Decimal when outcomes are finalized")
            if not Decimal(0) <= win_rate <= Decimal(1):
                raise AnalysisInputError("win_rate must lie between zero and one")
        if type(self.sufficient) is not bool:
            raise AnalysisInputError("sufficient must be a boolean")
        if not isinstance(self.pattern, IntelligencePattern):
            raise AnalysisInputError("pattern must be an IntelligencePattern label")
        if not isinstance(self.diagnostic, DiagnosticLabel):
            raise AnalysisInputError("diagnostic must be a DiagnosticLabel label")
        if self.rank is not None and (type(self.rank) is not int or self.rank < 1):
            raise AnalysisInputError("rank must be a positive integer or None")


def _validate_ranks(cells: tuple[IntelligenceCell, ...], settings: IntelligenceConfig) -> None:
    """Ranking eligibility and contiguous numbering hold within a dimension."""
    ranked = [cell.rank for cell in cells if cell.rank is not None]
    for cell in cells:
        if (cell.rank is not None) != _rank_eligible(cell, settings):
            raise AnalysisInputError("a cell is ranked exactly when it reaches the ranking minimum")
    if len(ranked) != len(set(ranked)):
        raise AnalysisInputError("ranks within a dimension must be unique")
    if ranked and sorted(ranked) != list(range(1, len(ranked) + 1)):
        raise AnalysisInputError("ranks must be consecutive from one")


@dataclass(frozen=True, slots=True)
class IntelligenceReport:
    """One deterministic strategy-intelligence research report.

    ``overall`` is the population cell over every Phase 21 validation signal
    provided. ``by_setup`` groups that population by the Phase 19 setup-type
    combination key; ``by_symbol``, ``by_timeframe``, and ``by_month`` group by
    signal-time symbol, timeframe, and UTC month. ``by_regime`` groups by the
    Phase 21 causal regime annotation carried on each row, placing rows without
    a regime into the ``unclassified`` bucket. Every non-regime family and the
    regime family partition the overall population, so each validation signal
    is described exactly once.

    The report is derived only from published Phase 21 facts; the layer never
    replays, re-detects regimes, recomputes outcomes, or re-runs Phase 21. It
    declares its research-only role and the not-approved Phase 23 optimization
    boundary; it never optimizes, selects, enables or disables a strategy,
    vetoes, or advises.
    """

    settings: IntelligenceConfig
    overall: IntelligenceCell
    by_setup: tuple[IntelligenceCell, ...]
    by_symbol: tuple[IntelligenceCell, ...]
    by_timeframe: tuple[IntelligenceCell, ...]
    by_month: tuple[IntelligenceCell, ...]
    by_regime: tuple[IntelligenceCell, ...]
    series_keys: tuple[str, ...]
    report_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.settings, IntelligenceConfig):
            raise AnalysisInputError("intelligence reports require IntelligenceConfig")
        if not isinstance(self.overall, IntelligenceCell):
            raise AnalysisInputError("intelligence reports require an overall cell")
        if (
            self.overall.dimension is not IntelligenceDimension.OVERALL
            or self.overall.name != "all"
        ):
            raise AnalysisInputError("the overall cell is the all-signals population")
        for name, cells, dimension in (
            ("by_setup", self.by_setup, IntelligenceDimension.SETUP),
            ("by_symbol", self.by_symbol, IntelligenceDimension.SYMBOL),
            ("by_timeframe", self.by_timeframe, IntelligenceDimension.TIMEFRAME),
            ("by_month", self.by_month, IntelligenceDimension.MONTH),
            ("by_regime", self.by_regime, IntelligenceDimension.REGIME),
        ):
            for cell in cells:
                if not isinstance(cell, IntelligenceCell) or cell.dimension is not dimension:
                    raise AnalysisInputError(
                        f"{name} cells must carry the {dimension.value} dimension"
                    )
            if cells and [cell.name for cell in cells] != sorted(cell.name for cell in cells):
                raise AnalysisInputError(f"{name} cells must be sorted by name")
            totals = sum(cell.total_buy_signals for cell in cells)
            if totals != self.overall.total_buy_signals:
                raise AnalysisInputError(f"{name} must partition the overall population")
            if cells:
                _validate_ranks(cells, self.settings)
        for key in self.series_keys:
            _text(key, "series key")
        if list(self.series_keys) != sorted(self.series_keys):
            raise AnalysisInputError("series keys must be sorted")
        if not self.series_keys:
            raise AnalysisInputError("a report covers at least one series")
        _text(self.report_id, "report_id")
        if not self.report_id.startswith("intelligence-report:"):
            raise AnalysisInputError("report ids use the intelligence-report prefix")
