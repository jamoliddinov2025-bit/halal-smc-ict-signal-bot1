"""Deterministic configuration artifacts, identities, and machine summaries."""

from __future__ import annotations

from hashlib import sha256

from smcsignal.analysis.backtest.models import BacktestConfiguration, BacktestSignalResult
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest, evidence_json
from smcsignal.analysis.performance.models import PerformanceBucket
from smcsignal.analysis.robustness.config import METHODOLOGY_VERSION, RobustnessConfig
from smcsignal.analysis.robustness.models import (
    DatasetRobustnessResult,
    RobustnessReport,
    SegmentStats,
)


def configuration_artifact(config: RobustnessConfig) -> bytes:
    """Frozen methodology artifact; the validation-only role is declared."""

    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "role": "robustness_validation_only",
            "optimization": False,
            "parameter_selection": False,
            "strategy_modification": False,
            "self_modification": False,
            "signal_generation": False,
            "signal_veto": False,
            "live_trading": False,
            "execution": False,
            "advice": False,
            "statistical_significance_claim": False,
        }
    )


def configuration_hash(config: RobustnessConfig) -> str:
    """SHA-256 of the frozen robustness artifact."""

    return sha256(configuration_artifact(config)).hexdigest()


def robustness_identity(
    backtest: BacktestConfiguration,
    settings: RobustnessConfig,
    results: list[DatasetRobustnessResult],
) -> str:
    """Stable identity from configurations, datasets, windows, and records."""

    from smcsignal.analysis.backtest.calculation import series_key_for
    from smcsignal.analysis.backtest.evidence import configuration_hash as backtest_hash

    payload = {
        "methodology": METHODOLOGY_VERSION,
        "backtest_configuration_hash": backtest_hash(backtest),
        "robustness_configuration_hash": configuration_hash(settings),
        "datasets": [
            {
                "series_key": series_key_for(result.dataset),
                "candles": len(result.dataset.candles),
                "windows": [
                    {
                        "window_index": window.window.window_index,
                        "development": [
                            window.window.development.start,
                            window.window.development.end,
                        ],
                        "validation": [
                            window.window.validation.start,
                            window.window.validation.end,
                        ],
                        "replay_id": window.replay_id,
                        "development_rows": [row.signal_id for row in window.development.rows],
                        "validation_rows": [row.signal_id for row in window.validation.rows],
                    }
                    for window in result.windows
                ],
            }
            for result in results
        ],
    }
    return f"robustness-report:{digest(payload)}"


def _bucket_payload(bucket: PerformanceBucket) -> dict[str, object]:
    return {
        "group": bucket.group,
        "name": bucket.name,
        "total_buy_signals": bucket.total_buy_signals,
        "open_count": bucket.open_count,
        "win_count": bucket.win_count,
        "loss_count": bucket.loss_count,
        "flat_count": bucket.flat_count,
        "finalized_count": bucket.finalized_count,
        "final_return_sum": str(bucket.final_return_sum),
        "mfe_return_sum": str(bucket.mfe_return_sum),
        "mae_return_sum": str(bucket.mae_return_sum),
        "win_rate": None if bucket.win_rate is None else str(bucket.win_rate),
        "average_final_return": (
            None if bucket.average_final_return is None else str(bucket.average_final_return)
        ),
        "average_mfe_return": (
            None if bucket.average_mfe_return is None else str(bucket.average_mfe_return)
        ),
        "average_mae_return": (
            None if bucket.average_mae_return is None else str(bucket.average_mae_return)
        ),
        "sufficient_sample": bucket.sufficient_sample,
    }


def _segment_payload(segment: SegmentStats) -> dict[str, object]:
    summary = segment.summary
    return {
        "kind": segment.kind,
        "label": segment.label,
        "regime": None if segment.regime is None else segment.regime.value,
        "total_buy_signals": summary.total_buy_signals,
        "open_count": summary.open_count,
        "win_count": summary.win_count,
        "loss_count": summary.loss_count,
        "flat_count": summary.flat_count,
        "finalized_count": summary.finalized_count,
        "final_return_sum": str(summary.final_return_sum),
        "mfe_return_sum": str(summary.mfe_return_sum),
        "mae_return_sum": str(summary.mae_return_sum),
        "win_rate": None if summary.win_rate is None else str(summary.win_rate),
        "average_final_return": (
            None if summary.average_final_return is None else str(summary.average_final_return)
        ),
        "average_mfe_return": (
            None if summary.average_mfe_return is None else str(summary.average_mfe_return)
        ),
        "average_mae_return": (
            None if summary.average_mae_return is None else str(summary.average_mae_return)
        ),
        "status": None if segment.status is None else segment.status.value,
    }


def _row_payload(row: BacktestSignalResult, regime: str | None) -> dict[str, object]:
    return {
        "signal_id": row.signal_id,
        "dataset_key": row.dataset_key,
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "candle_index": row.candle_index,
        "opened_at": row.opened_at.isoformat().replace("+00:00", "Z"),
        "score_total": row.score_total,
        "combination_key": row.combination_key,
        "outcome_status": row.outcome_status.value,
        "final_return": None if row.final_return is None else str(row.final_return),
        "mfe_return": None if row.mfe_return is None else str(row.mfe_return),
        "mae_return": None if row.mae_return is None else str(row.mae_return),
        "regime": regime,
    }


def summary_payload(report: RobustnessReport) -> dict[str, object]:
    """Deterministic machine-readable summary as canonical JSON data.

    Every value is copied from the Phase 18/19/20 records the report already
    holds; Decimal ratios remain exact strings. The payload is validation
    research output, not advice.
    """

    return {
        "methodology": METHODOLOGY_VERSION,
        "report_id": report.report_id,
        "configuration_hash": configuration_hash(report.settings),
        "series_keys": list(report.series_keys),
        "overall": _bucket_payload(report.overall),
        "by_symbol": [_bucket_payload(bucket) for bucket in report.by_symbol],
        "by_timeframe": [_bucket_payload(bucket) for bucket in report.by_timeframe],
        "by_combination": [_bucket_payload(bucket) for bucket in report.by_combination],
        "by_period": [
            {"dataset_key": row.dataset_key, **_segment_payload(row.segment)}
            for row in report.by_period
        ],
        "by_regime": [_segment_payload(segment) for segment in report.by_regime],
        "windows": [
            {
                "dataset_key": series_key_of(result),
                "window_index": window.window.window_index,
                "development": _segment_payload(window.development),
                "validation": _segment_payload(window.validation),
                "win_rate_degradation": (
                    None
                    if window.win_rate_degradation is None
                    else str(window.win_rate_degradation)
                ),
                "average_final_return_degradation": (
                    None
                    if window.average_final_return_degradation is None
                    else str(window.average_final_return_degradation)
                ),
                "replay_id": window.replay_id,
            }
            for result in report.datasets
            for window in result.windows
        ],
        "degradation": {
            "window_count": report.degradation.window_count,
            "comparable_window_count": report.degradation.comparable_window_count,
            "average_win_rate_degradation": (
                None
                if report.degradation.average_win_rate_degradation is None
                else str(report.degradation.average_win_rate_degradation)
            ),
            "average_final_return_degradation": (
                None
                if report.degradation.average_final_return_degradation is None
                else str(report.degradation.average_final_return_degradation)
            ),
        },
        "stability": {
            "validation_segment_count": report.stability.validation_segment_count,
            "sufficient_segment_count": report.stability.sufficient_segment_count,
            "positive_segment_count": report.stability.positive_segment_count,
            "negative_segment_count": report.stability.negative_segment_count,
            "win_rate_spread": (
                None
                if report.stability.win_rate_spread is None
                else str(report.stability.win_rate_spread)
            ),
            "average_final_return_spread": (
                None
                if report.stability.average_final_return_spread is None
                else str(report.stability.average_final_return_spread)
            ),
            "average_mfe_return_spread": (
                None
                if report.stability.average_mfe_return_spread is None
                else str(report.stability.average_mfe_return_spread)
            ),
            "average_mae_return_spread": (
                None
                if report.stability.average_mae_return_spread is None
                else str(report.stability.average_mae_return_spread)
            ),
            "best_period": report.stability.best_period,
            "worst_period": report.stability.worst_period,
            "status": report.stability.status.value,
        },
        "signals": [
            _row_payload(row, _regime_of(report, row))
            for result in report.datasets
            for row in result.validation_rows
        ],
    }


def series_key_of(result: DatasetRobustnessResult) -> str:
    from smcsignal.analysis.backtest.calculation import series_key_for

    return series_key_for(result.dataset)


def _regime_of(report: RobustnessReport, row: BacktestSignalResult) -> str | None:
    for result in report.datasets:
        if row.signal_id in result.row_regimes:
            regime = result.row_regimes[row.signal_id]
            return None if regime is None else regime.value
    raise ValueError("every validation row carries a regime annotation")


def machine_summary(report: RobustnessReport) -> bytes:
    """Canonical JSON bytes of the machine-readable robustness summary."""

    return evidence_json(summary_payload(report)).encode("utf-8")
