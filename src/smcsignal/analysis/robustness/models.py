"""Immutable walk-forward, regime, and robustness result facts. Not advice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from smcsignal.analysis.backtest.models import (
    BacktestConfiguration,
    BacktestSignalResult,
    ReplayDataset,
)
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking.models import AnalyticsSummary, OutcomeStatus
from smcsignal.analysis.performance.calculation import month_of
from smcsignal.analysis.performance.models import PerformanceBucket
from smcsignal.analysis.provenance import _instant
from smcsignal.analysis.robustness.config import RobustnessConfig


class MarketRegime(StrEnum):
    """Deterministic descriptive regime labels; never a signal or a filter."""

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"


class SegmentStatus(StrEnum):
    """Descriptive stability labels gated by configurable sample minimums."""

    STABLE = "STABLE"
    WEAK = "WEAK"
    UNDERSAMPLED = "UNDERSAMPLED"


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _ratio_check(value: Decimal | None, name: str) -> None:
    if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0):
        raise AnalysisInputError(f"{name} must be a nonnegative, finite Decimal or None")


@dataclass(frozen=True, slots=True)
class RegimeObservation:
    """One candle's causal regime annotation with its exact metric inputs.

    The observation at candle t is computed only from candles at or before t.
    Warmup candles (before the baseline window fills) carry ``None`` regime
    and metrics. A ``None`` volatility_ratio marks a degenerate flat baseline
    history where the ratio is undefined. This is a research annotation: it
    never generates, vetoes, or modifies signals and never alters outcomes.
    """

    index: int
    opened_at: datetime
    regime: MarketRegime | None
    efficiency_ratio: Decimal | None
    volatility_ratio: Decimal | None
    lookback_bars: int
    baseline_bars: int

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise AnalysisInputError("regime index must be a nonnegative integer")
        object.__setattr__(self, "opened_at", _instant(self.opened_at, "opened_at"))
        if self.regime is not None and not isinstance(self.regime, MarketRegime):
            raise AnalysisInputError("regime must be a MarketRegime label or None")
        if self.efficiency_ratio is None and self.regime is not None:
            raise AnalysisInputError("a regime label requires its warmup metrics")
        _ratio_check(self.efficiency_ratio, "efficiency_ratio")
        if self.efficiency_ratio is not None and self.efficiency_ratio > 1:
            raise AnalysisInputError("efficiency_ratio cannot exceed one")
        _ratio_check(self.volatility_ratio, "volatility_ratio")
        for name, value in (
            ("lookback_bars", self.lookback_bars),
            ("baseline_bars", self.baseline_bars),
        ):
            if type(value) is not int or value < 1:
                raise AnalysisInputError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class PeriodRange:
    """One contiguous candle-index range within one dataset.

    Indices are absolute dataset candle indexes; ``start`` is inclusive and
    ``end`` exclusive. The label is the UTC calendar month of the segment's
    first candle, produced by the existing Phase 19 month helper.
    """

    start: int
    end: int
    first_opened_at: datetime

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.end) is not int:
            raise AnalysisInputError("period bounds must be integers")
        if not 0 <= self.start < self.end:
            raise AnalysisInputError("period bounds must satisfy 0 <= start < end")
        object.__setattr__(
            self, "first_opened_at", _instant(self.first_opened_at, "first_opened_at")
        )

    @property
    def label(self) -> str:
        return month_of(self.first_opened_at)

    @property
    def bars(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """One sequential walk-forward window over one dataset.

    The window covers dataset candles ``[window_start, window_end)``. The
    development period precedes the validation period inside the window and
    the two never overlap; across windows, validation periods never overlap
    because the configured step is at least the validation length. Windows
    are self-contained replays: no candle outside the window influences its
    published facts, so no leakage between windows is possible.
    """

    window_index: int
    development: PeriodRange
    validation: PeriodRange

    def __post_init__(self) -> None:
        if type(self.window_index) is not int or self.window_index < 0:
            raise AnalysisInputError("window_index must be a nonnegative integer")
        development, validation = self.development, self.validation
        if not isinstance(development, PeriodRange) or not isinstance(validation, PeriodRange):
            raise AnalysisInputError("windows require development and validation periods")
        if development.end != validation.start:
            raise AnalysisInputError("the validation period must immediately follow development")
        if development.start >= development.end or validation.start >= validation.end:
            raise AnalysisInputError("periods must be nonempty")

    @property
    def window_start(self) -> int:
        return self.development.start

    @property
    def window_end(self) -> int:
        return self.validation.end


@dataclass(frozen=True, slots=True)
class SegmentStats:
    """Descriptive statistics of one period segment; Phase 18 arithmetic.

    ``summary`` is the existing Phase 18 ``AnalyticsSummary`` recomputed over
    the segment's rows with the existing aggregate helper, so every count,
    sum, and 50-digit ratio matches Phase 18/19 exactly. ``regime`` is the
    causal regime annotation at the segment's final candle. Status labels
    exist on validation segments only; development segments carry ``None``.
    """

    kind: str
    label: str
    regime: MarketRegime | None
    summary: AnalyticsSummary
    status: SegmentStatus | None
    rows: tuple[BacktestSignalResult, ...]

    def __post_init__(self) -> None:
        if self.kind not in ("development", "validation"):
            raise AnalysisInputError("segment kind must be development or validation")
        _text(self.label, "label")
        if self.regime is not None and not isinstance(self.regime, MarketRegime):
            raise AnalysisInputError("regime must be a MarketRegime label or None")
        if not isinstance(self.summary, AnalyticsSummary):
            raise AnalysisInputError("segment statistics require the Phase 18 AnalyticsSummary")
        if self.status is not None and not isinstance(self.status, SegmentStatus):
            raise AnalysisInputError("status must be a SegmentStatus label or None")
        if self.kind == "development" and self.status is not None:
            raise AnalysisInputError("development segments carry no stability status")
        if not isinstance(self.rows, tuple) or not all(
            isinstance(row, BacktestSignalResult) for row in self.rows
        ):
            raise AnalysisInputError("segments contain BacktestSignalResult rows only")
        if self.summary.total_buy_signals != len(self.rows):
            raise AnalysisInputError("segment summaries must cover exactly their rows")
        finalized = sum(row.outcome.status is not OutcomeStatus.OPEN for row in self.rows)
        if (self.summary.finalized_count, self.summary.open_count) != (
            finalized,
            len(self.rows) - finalized,
        ):
            raise AnalysisInputError("segment summaries must match the row outcome statuses")


@dataclass(frozen=True, slots=True)
class WindowResult:
    """One evaluated walk-forward window with its development/validation split."""

    window: WalkForwardWindow
    development: SegmentStats
    validation: SegmentStats
    win_rate_degradation: Decimal | None
    average_final_return_degradation: Decimal | None
    replay_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.window, WalkForwardWindow):
            raise AnalysisInputError("window results require a WalkForwardWindow")
        for name, segment in (("development", self.development), ("validation", self.validation)):
            if not isinstance(segment, SegmentStats) or segment.kind != name:
                raise AnalysisInputError(f"window results require a {name} SegmentStats")
        _ratio_check_signed(self.win_rate_degradation, "win_rate_degradation")
        _ratio_check_signed(
            self.average_final_return_degradation, "average_final_return_degradation"
        )
        _text(self.replay_id, "replay_id")
        if not self.replay_id.startswith("backtest-replay:"):
            raise AnalysisInputError("window results carry the Phase 20 replay identity")


def _ratio_check_signed(value: Decimal | None, name: str) -> None:
    if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
        raise AnalysisInputError(f"{name} must be a finite Decimal or None")


@dataclass(frozen=True, slots=True)
class DatasetRobustnessResult:
    """All walk-forward windows of one dataset plus its causal regime series.

    The regime series is computed over the dataset's full declared primary
    history; every observation is causal (uses only candles at or before its
    own index) in the same way the Phase 20 MTF join consumes HTF history.
    ``row_regimes`` maps every window signal id to the regime annotation at
    its signal candle (``None`` during warmup).
    """

    dataset: ReplayDataset
    windows: tuple[WindowResult, ...]
    regime_series: tuple[RegimeObservation, ...]
    row_regimes: Mapping[str, MarketRegime | None]

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, ReplayDataset):
            raise AnalysisInputError("dataset results require a ReplayDataset")
        if not isinstance(self.windows, tuple) or not self.windows:
            raise AnalysisInputError("dataset results require at least one window result")
        previous: WalkForwardWindow | None = None
        for position, result in enumerate(self.windows):
            if not isinstance(result, WindowResult):
                raise AnalysisInputError("dataset results contain window results only")
            if result.window.window_index != position:
                raise AnalysisInputError("window results must be indexed in chronological order")
            if previous is not None:
                if result.window.window_start < previous.window_start:
                    raise AnalysisInputError("windows must advance chronologically")
                if result.window.validation.start < previous.validation.end:
                    raise AnalysisInputError("validation periods must never overlap")
            previous = result.window
        if not isinstance(self.regime_series, tuple) or not all(
            isinstance(observation, RegimeObservation) for observation in self.regime_series
        ):
            raise AnalysisInputError("dataset results carry a regime observation series")
        if [observation.index for observation in self.regime_series] != list(
            range(len(self.regime_series))
        ):
            raise AnalysisInputError("regime observations must be indexed in candle order")
        if not isinstance(self.row_regimes, Mapping):
            raise AnalysisInputError("row regimes must be a mapping of signal ids to regimes")
        object.__setattr__(self, "row_regimes", MappingProxyType(dict(self.row_regimes)))
        for result in self.windows:
            for segment in (result.development, result.validation):
                for row in segment.rows:
                    if row.signal_id not in self.row_regimes:
                        raise AnalysisInputError("every window row carries a regime annotation")

    @property
    def validation_rows(self) -> tuple[BacktestSignalResult, ...]:
        """Every out-of-sample row across the windows, in window order."""

        return tuple(row for result in self.windows for row in result.validation.rows)

    @property
    def validation_segments(self) -> tuple[SegmentStats, ...]:
        return tuple(result.validation for result in self.windows)


@dataclass(frozen=True, slots=True)
class DegradationSummary:
    """Development-to-validation degradation across comparable windows.

    A window is comparable when both of its segments reach the configured
    minimum finalized count. Degradations are exact Decimal deltas
    (validation minus development); negative values mean the validation
    period underperformed its development reference. No significance claim.
    """

    window_count: int
    comparable_window_count: int
    average_win_rate_degradation: Decimal | None
    average_final_return_degradation: Decimal | None

    def __post_init__(self) -> None:
        for name, value in (
            ("window_count", self.window_count),
            ("comparable_window_count", self.comparable_window_count),
        ):
            if type(value) is not int or value < 0:
                raise AnalysisInputError(f"{name} must be a nonnegative integer")
        if self.comparable_window_count > self.window_count:
            raise AnalysisInputError("comparable windows cannot exceed all windows")
        _ratio_check_signed(self.average_win_rate_degradation, "average_win_rate_degradation")
        _ratio_check_signed(
            self.average_final_return_degradation, "average_final_return_degradation"
        )


@dataclass(frozen=True, slots=True)
class StabilitySummary:
    """Descriptive stability indicators over validation segments.

    Spreads are max-minus-min over segments that reach the configured minimum
    finalized count; best and worst periods quote that segment's label. The
    status is UNDERSAMPLED when too few segments reach the minimum, STABLE
    when every sufficient segment is STABLE and the win-rate spread stays
    within the configured maximum, and WEAK otherwise. These are descriptive
    labels over published records; no statistical significance is claimed.
    """

    validation_segment_count: int
    sufficient_segment_count: int
    positive_segment_count: int
    negative_segment_count: int
    win_rate_spread: Decimal | None
    average_final_return_spread: Decimal | None
    average_mfe_return_spread: Decimal | None
    average_mae_return_spread: Decimal | None
    best_period: str | None
    worst_period: str | None
    status: SegmentStatus

    def __post_init__(self) -> None:
        for name, value in (
            ("validation_segment_count", self.validation_segment_count),
            ("sufficient_segment_count", self.sufficient_segment_count),
            ("positive_segment_count", self.positive_segment_count),
            ("negative_segment_count", self.negative_segment_count),
        ):
            if type(value) is not int or value < 0:
                raise AnalysisInputError(f"{name} must be a nonnegative integer")
        if self.sufficient_segment_count > self.validation_segment_count:
            raise AnalysisInputError("sufficient segments cannot exceed all validation segments")
        if (
            self.positive_segment_count + self.negative_segment_count
            > self.sufficient_segment_count
        ):
            raise AnalysisInputError("positive and negative segments cannot exceed sufficient ones")
        for spread_name, spread in (
            ("win_rate_spread", self.win_rate_spread),
            ("average_final_return_spread", self.average_final_return_spread),
            ("average_mfe_return_spread", self.average_mfe_return_spread),
            ("average_mae_return_spread", self.average_mae_return_spread),
        ):
            _ratio_check_signed(spread, spread_name)
        for period_name, period in (
            ("best_period", self.best_period),
            ("worst_period", self.worst_period),
        ):
            if period is not None:
                _text(period, period_name)
        if (self.best_period is None) != (self.worst_period is None):
            raise AnalysisInputError("best and worst periods appear together")
        if not isinstance(self.status, SegmentStatus):
            raise AnalysisInputError("status must be a SegmentStatus label")


@dataclass(frozen=True, slots=True)
class PeriodRow:
    """One validation segment positioned in its dataset for report listings."""

    dataset_key: str
    segment: SegmentStats

    def __post_init__(self) -> None:
        _text(self.dataset_key, "dataset_key")
        if not isinstance(self.segment, SegmentStats) or self.segment.kind != "validation":
            raise AnalysisInputError("period rows list validation segments only")


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    """One deterministic multi-dataset walk-forward robustness report.

    Cross-dataset aggregates (overall, symbol, timeframe, combination) are the
    existing Phase 19 ``PerformanceBucket`` recomputed with the Phase 18
    aggregate helpers over validation rows only — every out-of-sample signal
    is counted exactly once because validation segments never overlap.
    Development rows appear only inside per-window results and degradation
    deltas. Period and regime listings use the segment statistics. Nothing is
    optimized, selected, or advised; this is validation research output.
    """

    settings: RobustnessConfig
    backtest: BacktestConfiguration
    datasets: tuple[DatasetRobustnessResult, ...]
    overall: PerformanceBucket
    by_symbol: tuple[PerformanceBucket, ...]
    by_timeframe: tuple[PerformanceBucket, ...]
    by_combination: tuple[PerformanceBucket, ...]
    by_period: tuple[PeriodRow, ...]
    by_regime: tuple[SegmentStats, ...]
    degradation: DegradationSummary
    stability: StabilitySummary
    series_keys: tuple[str, ...]
    report_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.settings, RobustnessConfig):
            raise AnalysisInputError("robustness reports require RobustnessConfig")
        if not isinstance(self.backtest, BacktestConfiguration):
            raise AnalysisInputError("robustness reports require a BacktestConfiguration")
        if not isinstance(self.datasets, tuple) or not self.datasets:
            raise AnalysisInputError("robustness reports cover at least one dataset")
        keys: list[str] = []
        for result in self.datasets:
            if not isinstance(result, DatasetRobustnessResult):
                raise AnalysisInputError("robustness reports contain dataset results only")
            keys.append(
                f"{result.dataset.symbol}:{result.dataset.timeframe}:"
                f"{result.dataset.venue}:{result.dataset.provider}:{result.dataset.dataset_id}"
            )
        if len(set(keys)) != len(keys):
            raise AnalysisInputError("each dataset is evaluated exactly once")
        if list(self.series_keys) != sorted(keys):
            raise AnalysisInputError("series keys must match the evaluated datasets, sorted")
        if not isinstance(self.overall, PerformanceBucket) or self.overall.group != "overall":
            raise AnalysisInputError("the overall bucket is the all-signals group")
        for group, buckets in (
            ("symbol", self.by_symbol),
            ("timeframe", self.by_timeframe),
            ("combination", self.by_combination),
        ):
            for bucket in buckets:
                if not isinstance(bucket, PerformanceBucket) or bucket.group != group:
                    raise AnalysisInputError(f"a {group} bucket must declare its group")
            if [bucket.name for bucket in buckets] != sorted(bucket.name for bucket in buckets):
                raise AnalysisInputError(f"{group} buckets must be sorted by name")
        for name, buckets in (("symbol", self.by_symbol), ("timeframe", self.by_timeframe)):
            if not buckets:
                continue
            totals = sum(bucket.total_buy_signals for bucket in buckets)
            if totals != self.overall.total_buy_signals:
                raise AnalysisInputError(f"{name} buckets must partition the overall population")
        if not isinstance(self.by_period, tuple) or not all(
            isinstance(row, PeriodRow) for row in self.by_period
        ):
            raise AnalysisInputError("period listings contain period rows only")
        known = set(keys)
        for row in self.by_period:
            if row.dataset_key not in known:
                raise AnalysisInputError("period rows must reference an evaluated dataset")
        if not isinstance(self.by_regime, tuple) or not all(
            isinstance(segment, SegmentStats) for segment in self.by_regime
        ):
            raise AnalysisInputError("regime listings contain segment statistics only")
        for segment in self.by_regime:
            if segment.kind != "validation":
                raise AnalysisInputError("regime listings cover validation segments only")
        if [segment.label for segment in self.by_regime] != sorted(
            segment.label for segment in self.by_regime
        ):
            raise AnalysisInputError("regime segments must be sorted by label")
        if not isinstance(self.degradation, DegradationSummary):
            raise AnalysisInputError("robustness reports require a DegradationSummary")
        if not isinstance(self.stability, StabilitySummary):
            raise AnalysisInputError("robustness reports require a StabilitySummary")
        total_rows = sum(len(result.validation_rows) for result in self.datasets)
        if self.overall.total_buy_signals != total_rows:
            raise AnalysisInputError("the overall bucket must cover every validation row")
        _text(self.report_id, "report_id")
        if not self.report_id.startswith("robustness-report:"):
            raise AnalysisInputError("report ids use the robustness-report prefix")
