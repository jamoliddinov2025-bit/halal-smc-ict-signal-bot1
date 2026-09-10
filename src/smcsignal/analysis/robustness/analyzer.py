"""Walk-forward robustness evaluation over the existing Phase 20 replay."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from smcsignal.analysis.backtest.calculation import (
    replay_rows,
    series_key_for,
)
from smcsignal.analysis.backtest.models import (
    BacktestConfiguration,
    BacktestSignalResult,
    ReplayDataset,
)
from smcsignal.analysis.backtest.replay import replay_history
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.outcome_tracking.models import OutcomeStatus
from smcsignal.analysis.performance.calculation import bucket_for
from smcsignal.analysis.performance.models import PerformanceBucket
from smcsignal.analysis.robustness.calculation import (
    build_segment,
    degradation_summary,
    plan_windows,
    split_rows,
    stability_summary,
    window_degradation,
)
from smcsignal.analysis.robustness.config import RobustnessConfig
from smcsignal.analysis.robustness.evidence import robustness_identity
from smcsignal.analysis.robustness.models import (
    DatasetRobustnessResult,
    MarketRegime,
    PeriodRow,
    RegimeObservation,
    RobustnessReport,
    SegmentStats,
    WindowResult,
)
from smcsignal.analysis.robustness.regime import analyze_regimes

UNCLASSIFIED_REGIME = "unclassified"


def _regime_index(series: tuple[RegimeObservation, ...]) -> dict[datetime, MarketRegime | None]:
    """Timestamp-indexed regime lookup over one dataset's observation series."""

    return {observation.opened_at: observation.regime for observation in series}


def _window_dataset(dataset: ReplayDataset, start: int, end: int) -> ReplayDataset:
    """One self-contained window slice under the dataset's own identity."""

    return ReplayDataset(
        symbol=dataset.symbol,
        timeframe=dataset.timeframe,
        candles=dataset.candles[start:end],
        higher_candles=dataset.higher_candles,
        venue=dataset.venue,
        provider=dataset.provider,
        dataset_id=dataset.dataset_id,
    )


def evaluate_dataset(
    dataset: ReplayDataset,
    backtest: BacktestConfiguration,
    config: RobustnessConfig | None = None,
) -> DatasetRobustnessResult:
    """Plan and evaluate every walk-forward window of one dataset.

    Each window is one independent Phase 20 replay of its own candle slice
    (development followed by validation), so no candle outside the window can
    influence its published facts. Regime annotations come from the causal
    full-history series; a row's regime is the observation at its signal
    candle, and a segment label carries the observation at the segment's
    final candle. Outcomes stay exactly as Phase 18 published them at the
    window's end: open outcomes are never flushed.
    """

    if not isinstance(dataset, ReplayDataset):
        raise AnalysisInputError("robustness evaluation requires a ReplayDataset")
    if not isinstance(backtest, BacktestConfiguration):
        raise AnalysisInputError("robustness evaluation requires a BacktestConfiguration")
    if config is not None and not isinstance(config, RobustnessConfig):
        raise AnalysisConfigurationError("config must be RobustnessConfig")
    settings = config if config is not None else RobustnessConfig()
    windows = plan_windows(dataset, settings)
    if not windows:
        raise AnalysisInputError(
            "the dataset is shorter than one walk-forward window "
            f"(development {settings.development_bars} + validation {settings.validation_bars} "
            f"bars over {len(dataset.candles)} candles)"
        )
    regime_series = analyze_regimes(dataset.candles, settings)
    regimes_by_time = _regime_index(regime_series)
    results: list[WindowResult] = []
    row_regimes: dict[str, MarketRegime | None] = {}
    for window in windows:
        replay = replay_history(
            _window_dataset(dataset, window.window_start, window.window_end), backtest
        )
        rows = replay_rows(replay)
        development_rows, validation_rows = split_rows(rows, settings.development_bars)
        development = build_segment(
            "development",
            development_rows,
            label=window.development.label,
            regime=regimes_by_time[dataset.candles[window.development.end - 1].timestamp],
            config=settings,
        )
        validation = build_segment(
            "validation",
            validation_rows,
            label=window.validation.label,
            regime=regimes_by_time[dataset.candles[window.validation.end - 1].timestamp],
            config=settings,
        )
        win_rate, average = window_degradation(development, validation)
        results.append(
            WindowResult(
                window=window,
                development=development,
                validation=validation,
                win_rate_degradation=win_rate,
                average_final_return_degradation=average,
                replay_id=replay.replay_id,
            )
        )
        for row in rows:
            row_regimes[row.signal_id] = regimes_by_time[row.opened_at]
    return DatasetRobustnessResult(
        dataset=dataset,
        windows=tuple(results),
        regime_series=regime_series,
        row_regimes=row_regimes,
    )


def _buckets(
    group: str,
    grouped: Mapping[str, list[BacktestSignalResult]],
    settings: BacktestConfiguration,
) -> tuple[PerformanceBucket, ...]:
    buckets: list[PerformanceBucket] = []
    for name in sorted(grouped):
        rows = tuple(grouped[name])
        records = tuple(row.outcome for row in rows)
        open_count = sum(record.status is OutcomeStatus.OPEN for record in records)
        buckets.append(
            bucket_for(
                group,
                name,
                records,
                total_buy_signals=len(rows),
                open_count=open_count,
                settings=settings.performance,
            )
        )
    return tuple(buckets)


def run_robustness(
    datasets: Iterable[ReplayDataset],
    backtest: BacktestConfiguration,
    config: RobustnessConfig | None = None,
) -> RobustnessReport:
    """Evaluate every dataset and compose one deterministic robustness report.

    Cross-dataset aggregates use validation rows only — every out-of-sample
    signal is counted exactly once because validation segments never overlap.
    Development rows appear only in per-window results and degradation
    deltas. Nothing is fitted, selected, or optimized anywhere.
    """

    if not isinstance(backtest, BacktestConfiguration):
        raise AnalysisInputError("robustness evaluation requires a BacktestConfiguration")
    if config is not None and not isinstance(config, RobustnessConfig):
        raise AnalysisConfigurationError("config must be RobustnessConfig")
    settings = config if config is not None else RobustnessConfig()
    try:
        materialized = tuple(datasets)
    except TypeError as exc:
        raise AnalysisInputError("datasets must be an iterable of ReplayDataset records") from exc
    if not materialized:
        raise AnalysisInputError("robustness evaluation covers at least one dataset")
    evaluated: list[DatasetRobustnessResult] = []
    keys: list[str] = []
    for dataset in materialized:
        if not isinstance(dataset, ReplayDataset):
            raise AnalysisInputError("datasets must be ReplayDataset records")
        key = series_key_for(dataset)
        if key in keys:
            raise AnalysisInputError("each dataset is evaluated exactly once: " + key)
        keys.append(key)
        evaluated.append(evaluate_dataset(dataset, backtest, settings))
    order = sorted(range(len(evaluated)), key=lambda position: keys[position])
    evaluated = [evaluated[position] for position in order]
    keys = [keys[position] for position in order]

    all_windows: list[WindowResult] = [
        result for dataset_result in evaluated for result in dataset_result.windows
    ]
    all_validation: list[SegmentStats] = [
        segment for dataset_result in evaluated for segment in dataset_result.validation_segments
    ]
    rows: list[BacktestSignalResult] = [
        row for dataset_result in evaluated for row in dataset_result.validation_rows
    ]

    overall_records = tuple(row.outcome for row in rows)
    overall = bucket_for(
        "overall",
        "all",
        overall_records,
        total_buy_signals=len(rows),
        open_count=sum(record.status is OutcomeStatus.OPEN for record in overall_records),
        settings=backtest.performance,
    )
    symbols: dict[str, list[BacktestSignalResult]] = {}
    timeframes: dict[str, list[BacktestSignalResult]] = {}
    combinations: dict[str, list[BacktestSignalResult]] = {}
    regimes: dict[str, list[BacktestSignalResult]] = {}
    for dataset_result in evaluated:
        for row in dataset_result.validation_rows:
            symbols.setdefault(row.symbol, []).append(row)
            timeframes.setdefault(row.timeframe, []).append(row)
            combinations.setdefault(row.combination_key, []).append(row)
            regime = dataset_result.row_regimes[row.signal_id]
            regimes.setdefault(
                regime.value if regime is not None else UNCLASSIFIED_REGIME, []
            ).append(row)
    by_symbol = _buckets("symbol", symbols, backtest)
    by_timeframe = _buckets("timeframe", timeframes, backtest)
    by_combination = _buckets("combination", combinations, backtest)
    by_regime = tuple(
        build_segment(
            "validation",
            regime_rows,
            label=name,
            regime=MarketRegime(name) if name != UNCLASSIFIED_REGIME else None,
            config=settings,
        )
        for name, regime_rows in sorted(regimes.items())
    )
    by_period = tuple(
        PeriodRow(series_key_for(dataset_result.dataset), dataset_result.validation_segments[index])
        for dataset_result in evaluated
        for index in range(len(dataset_result.windows))
    )
    degradation = degradation_summary(all_windows, settings)
    stability = stability_summary(all_validation, settings)
    report_id = robustness_identity(backtest, settings, evaluated)
    return RobustnessReport(
        settings=settings,
        backtest=backtest,
        datasets=tuple(evaluated),
        overall=overall,
        by_symbol=by_symbol,
        by_timeframe=by_timeframe,
        by_combination=by_combination,
        by_period=by_period,
        by_regime=by_regime,
        degradation=degradation,
        stability=stability,
        series_keys=tuple(keys),
        report_id=report_id,
    )
