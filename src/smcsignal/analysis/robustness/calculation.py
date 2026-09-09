"""Pure walk-forward planning and robustness arithmetic over published rows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.backtest.models import BacktestSignalResult, ReplayDataset
from smcsignal.analysis.displacement.calculation import difference, ratio
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking.calculation import aggregate
from smcsignal.analysis.outcome_tracking.models import AnalyticsSummary, OutcomeStatus
from smcsignal.analysis.robustness.config import RobustnessConfig
from smcsignal.analysis.robustness.models import (
    DegradationSummary,
    MarketRegime,
    PeriodRange,
    SegmentStats,
    SegmentStatus,
    StabilitySummary,
    WalkForwardWindow,
    WindowResult,
)


def plan_windows(dataset: ReplayDataset, config: RobustnessConfig) -> tuple[WalkForwardWindow, ...]:
    """Plan the sequential chronological walk-forward windows of one dataset.

    Window k starts at dataset candle ``k * step_bars`` and pairs a
    development period of ``development_bars`` candles with the validation
    period of ``validation_bars`` candles that immediately follows it. A
    trailing stretch that cannot hold one full window is dropped. Validation
    periods never overlap because the configuration requires
    ``step_bars >= validation_bars``; development periods may overlap. An
    empty plan means the dataset is shorter than one window.
    """

    if not isinstance(dataset, ReplayDataset):
        raise AnalysisInputError("window planning requires a ReplayDataset")
    if not isinstance(config, RobustnessConfig):
        raise AnalysisInputError("window planning requires a RobustnessConfig")
    candles = dataset.candles
    development_bars = config.development_bars
    validation_bars = config.validation_bars
    windows: list[WalkForwardWindow] = []
    start = 0
    while start + development_bars + validation_bars <= len(candles):
        development = PeriodRange(start, start + development_bars, candles[start].timestamp)
        validation = PeriodRange(
            start + development_bars,
            start + development_bars + validation_bars,
            candles[start + development_bars].timestamp,
        )
        windows.append(WalkForwardWindow(len(windows), development, validation))
        start += config.step_bars
    return tuple(windows)


def split_rows(
    rows: Sequence[BacktestSignalResult], development_bars: int
) -> tuple[tuple[BacktestSignalResult, ...], tuple[BacktestSignalResult, ...]]:
    """Split one window replay's rows by their window-relative candle index."""

    if type(development_bars) is not int or development_bars < 1:
        raise AnalysisInputError("development_bars must be a positive integer")
    development = tuple(row for row in rows if row.candle_index < development_bars)
    validation = tuple(row for row in rows if row.candle_index >= development_bars)
    return development, validation


def segment_summary(rows: Sequence[BacktestSignalResult]) -> AnalyticsSummary:
    """Phase 18 aggregate over one segment's rows; exact Decimal arithmetic."""

    records = tuple(row.outcome for row in rows)
    finalized = tuple(record for record in records if record.status is not OutcomeStatus.OPEN)
    return aggregate(
        finalized, total_buy_signals=len(records), open_count=len(records) - len(finalized)
    )


def segment_status(summary: AnalyticsSummary, config: RobustnessConfig) -> SegmentStatus:
    """Descriptive status of one validation segment under the configured minimums."""

    if not isinstance(config, RobustnessConfig):
        raise AnalysisInputError("segment status requires RobustnessConfig")
    if summary.finalized_count < config.minimum_finalized_for_stability:
        return SegmentStatus.UNDERSAMPLED
    win_rate = summary.win_rate
    average = summary.average_final_return
    assert win_rate is not None and average is not None  # finalized outcomes exist
    if win_rate >= config.stability_win_rate_floor and average >= 0:
        return SegmentStatus.STABLE
    return SegmentStatus.WEAK


def build_segment(
    kind: str,
    rows: Sequence[BacktestSignalResult],
    *,
    label: str,
    regime: MarketRegime | None,
    config: RobustnessConfig,
) -> SegmentStats:
    """One segment's statistics, with a stability status on validation only."""

    summary = segment_summary(rows)
    status = segment_status(summary, config) if kind == "validation" else None
    ordered = tuple(sorted(rows, key=lambda row: (row.opened_at, row.signal_id)))
    return SegmentStats(
        kind=kind,
        label=label,
        regime=regime,
        summary=summary,
        status=status,
        rows=ordered,
    )


def window_degradation(
    development: SegmentStats, validation: SegmentStats
) -> tuple[Decimal | None, Decimal | None]:
    """Exact validation-minus-development deltas; None when a side is undefined."""

    if development.summary.win_rate is None or validation.summary.win_rate is None:
        return None, None
    win_rate = difference(validation.summary.win_rate, development.summary.win_rate)
    dev_average = development.summary.average_final_return
    val_average = validation.summary.average_final_return
    average = (
        None if dev_average is None or val_average is None else difference(val_average, dev_average)
    )
    return win_rate, average


def degradation_summary(
    windows: Sequence[WindowResult], config: RobustnessConfig
) -> DegradationSummary:
    """Average degradation across windows whose segments are both sufficient."""

    if not isinstance(config, RobustnessConfig):
        raise AnalysisInputError("degradation requires RobustnessConfig")
    minimum = config.minimum_finalized_for_stability
    deltas: list[Decimal] = []
    averages: list[Decimal] = []
    comparable = 0
    for result in windows:
        both = (
            result.development.summary.finalized_count >= minimum
            and result.validation.summary.finalized_count >= minimum
        )
        if not both:
            continue
        comparable += 1
        win_rate, average = window_degradation(result.development, result.validation)
        assert win_rate is not None  # both sides have finalized outcomes
        deltas.append(win_rate)
        if average is not None:
            averages.append(average)
    mean_win_rate = ratio(sum(deltas, Decimal(0)), Decimal(len(deltas))) if deltas else None
    mean_average = ratio(sum(averages, Decimal(0)), Decimal(len(averages))) if averages else None
    return DegradationSummary(
        window_count=len(windows),
        comparable_window_count=comparable,
        average_win_rate_degradation=mean_win_rate,
        average_final_return_degradation=mean_average,
    )


def _sufficient(segment: SegmentStats, config: RobustnessConfig) -> bool:
    return segment.summary.finalized_count >= config.minimum_finalized_for_stability


def stability_summary(
    segments: Sequence[SegmentStats], config: RobustnessConfig
) -> StabilitySummary:
    """Descriptive stability indicators over validation segments."""

    if not isinstance(config, RobustnessConfig):
        raise AnalysisInputError("stability requires RobustnessConfig")
    sufficient = [segment for segment in segments if _sufficient(segment, config)]
    win_rates = [segment.summary.win_rate for segment in sufficient]
    assert all(rate is not None for rate in win_rates)
    spreads: dict[str, Decimal | None] = {}
    for name, attribute in (
        ("win_rate", "win_rate"),
        ("average_final_return", "average_final_return"),
        ("average_mfe_return", "average_mfe_return"),
        ("average_mae_return", "average_mae_return"),
    ):
        values = [
            value
            for value in (getattr(segment.summary, attribute) for segment in sufficient)
            if value is not None
        ]
        spreads[name] = difference(max(values), min(values)) if len(values) > 1 else None
    best: str | None = None
    worst: str | None = None
    if sufficient:
        averages: list[tuple[Decimal, int]] = []
        for position, segment in enumerate(sufficient):
            average = segment.summary.average_final_return
            assert average is not None  # sufficient segments have finalized outcomes
            averages.append((average, position))
        best = sufficient[max(averages, key=lambda item: item[0])[1]].label
        worst = sufficient[min(averages, key=lambda item: item[0])[1]].label
    positive = sum(
        1
        for segment in sufficient
        if segment.summary.average_final_return is not None
        and segment.summary.average_final_return > 0
    )
    negative = sum(
        1
        for segment in sufficient
        if segment.summary.average_final_return is not None
        and segment.summary.average_final_return < 0
    )
    if len(sufficient) < config.minimum_windows_for_stability:
        status = SegmentStatus.UNDERSAMPLED
    else:
        all_stable = all(segment.status is SegmentStatus.STABLE for segment in sufficient)
        spread = spreads["win_rate"]
        status = (
            SegmentStatus.STABLE
            if all_stable and (spread is None or spread <= config.maximum_win_rate_spread)
            else SegmentStatus.WEAK
        )
    return StabilitySummary(
        validation_segment_count=len(segments),
        sufficient_segment_count=len(sufficient),
        positive_segment_count=positive,
        negative_segment_count=negative,
        win_rate_spread=spreads["win_rate"],
        average_final_return_spread=spreads["average_final_return"],
        average_mfe_return_spread=spreads["average_mfe_return"],
        average_mae_return_spread=spreads["average_mae_return"],
        best_period=best,
        worst_period=worst,
        status=status,
    )


def period_label(opened_at: datetime) -> str:
    """UTC calendar-month label of a segment's first candle (Phase 19 helper)."""

    from smcsignal.analysis.performance.calculation import month_of

    return month_of(opened_at)
