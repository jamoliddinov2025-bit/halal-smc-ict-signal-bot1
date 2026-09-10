"""Deterministic configuration artifacts, candle prefixes, and backtest identities."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from smcsignal.analysis.backtest.config import METHODOLOGY_VERSION
from smcsignal.analysis.backtest.models import (
    BacktestConfiguration,
    BacktestReport,
    ReplayDataset,
    ReplayResult,
    ReplayStep,
)
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest, evidence_json
from smcsignal.analysis.performance.models import PerformanceBucket
from smcsignal.data.models import OHLCV


def configuration_artifact(configuration: BacktestConfiguration) -> bytes:
    """Frozen replay artifact; the historical-research-only role is declared."""

    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "backtest": configuration.backtest,
            "pipeline": {
                "analysis": configuration.analysis,
                "liquidity": configuration.liquidity,
                "displacement": configuration.displacement,
                "fvg": configuration.fvg,
                "order_blocks": configuration.order_blocks,
                "premium_discount": configuration.premium_discount,
                "ote": configuration.ote,
                "mtf": configuration.mtf,
                "halal_filter": configuration.halal_filter,
                "setup_quality": configuration.setup_quality,
                "signal_eligibility": configuration.signal_eligibility,
                "signal_engine": configuration.signal_engine,
                "outcome_tracking": configuration.outcome_tracking,
                "setup_attribution": configuration.setup_attribution,
                "performance": configuration.performance,
            },
            "role": "historical_replay_only",
            "live_trading": False,
            "execution": False,
            "optimization": False,
            "self_modification": False,
            "advice": False,
        }
    )


def configuration_hash(configuration: BacktestConfiguration) -> str:
    """SHA-256 of the frozen replay artifact."""

    return sha256(configuration_artifact(configuration)).hexdigest()


class CandlePrefixHasher:
    """Rolling SHA-256 over the exact candles drawn into one series' replay."""

    def __init__(self, dataset: ReplayDataset, timeframe: str) -> None:
        self._hasher = sha256(
            candle_prefix_seed(
                dataset.symbol, timeframe, dataset.venue, dataset.provider, dataset.dataset_id
            )
        )

    def extend(self, candle: OHLCV) -> None:
        """Fold one drawn candle into the rolling prefix digest."""

        encoded = canonical_bytes(candle)
        self._hasher.update(len(encoded).to_bytes(8, "big") + encoded)

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


def candle_prefix_seed(
    symbol: str, timeframe: str, venue: str, provider: str, dataset_id: str
) -> bytes:
    """Seed bytes for one series' rolling candle-prefix digest."""

    return canonical_bytes(
        {
            "schema": "backtest-ohlcv-prefix-v1",
            "symbol": symbol,
            "timeframe": timeframe,
            "venue": venue,
            "provider": provider,
            "dataset_id": dataset_id,
        }
    )


def replay_identity(
    configuration: BacktestConfiguration,
    dataset: ReplayDataset,
    primary_prefix: str,
    higher_prefixes: Mapping[str, str],
    steps: tuple[ReplayStep, ...],
) -> str:
    """Stable identity from configuration, dataset, fed candles, and records.

    The digest covers the exact candle prefixes that flowed through the
    chain (primary and every higher timeframe) plus every published record
    id, so any change to the history, the configuration, or a published
    fact changes the identity while identical runs stay identical.
    """

    payload = {
        "methodology": METHODOLOGY_VERSION,
        "configuration_hash": configuration_hash(configuration),
        "dataset": {
            "symbol": dataset.symbol,
            "timeframe": dataset.timeframe,
            "venue": dataset.venue,
            "provider": dataset.provider,
            "dataset_id": dataset.dataset_id,
        },
        "input_prefix_hash": primary_prefix,
        "higher_prefix_hashes": dict(sorted(higher_prefixes.items())),
        "records": [
            {
                "index": step.index,
                "signal": step.signal.provenance.evidence_id,
                "outcome": step.outcome.provenance.evidence_id,
                "attribution": step.attribution.provenance.evidence_id,
            }
            for step in steps
        ],
    }
    return f"backtest-replay:{digest(payload)}"


def backtest_identity(
    configuration: BacktestConfiguration,
    replays: tuple[ReplayResult, ...],
    performance_id: str,
) -> str:
    """Stable identity of one complete multi-dataset backtest."""

    payload = {
        "methodology": METHODOLOGY_VERSION,
        "configuration_hash": configuration_hash(configuration),
        "replays": [
            {
                "dataset": {
                    "symbol": replay.dataset.symbol,
                    "timeframe": replay.dataset.timeframe,
                    "venue": replay.dataset.venue,
                    "provider": replay.dataset.provider,
                    "dataset_id": replay.dataset.dataset_id,
                },
                "replay_id": replay.replay_id,
            }
            for replay in replays
        ],
        "performance_report": performance_id,
    }
    return f"backtest:{digest(payload)}"


def summary_payload(report: BacktestReport) -> dict[str, object]:
    """Deterministic machine-readable summary as canonical JSON data.

    Every value is copied from the existing Phase 18/19 records and the
    composed performance report; Decimal prices and ratios remain exact
    strings. The payload is a research record, not advice.
    """

    def _bucket(bucket: PerformanceBucket) -> dict[str, object]:
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

    performance = report.performance
    return {
        "methodology": METHODOLOGY_VERSION,
        "backtest_id": report.backtest_id,
        "configuration_hash": configuration_hash(report.configuration),
        "series_keys": list(performance.series_keys),
        "datasets": [
            {
                "symbol": replay.dataset.symbol,
                "timeframe": replay.dataset.timeframe,
                "venue": replay.dataset.venue,
                "provider": replay.dataset.provider,
                "dataset_id": replay.dataset.dataset_id,
                "replay_id": replay.replay_id,
                "candles": len(replay.dataset.candles),
            }
            for replay in report.replays
        ],
        "overall": _bucket(performance.overall),
        "by_symbol": [_bucket(bucket) for bucket in performance.by_symbol],
        "by_timeframe": [_bucket(bucket) for bucket in performance.by_timeframe],
        "by_label": [_bucket(bucket) for bucket in performance.by_label],
        "by_combination": [_bucket(bucket) for bucket in performance.by_combination],
        "by_month": [_bucket(bucket) for bucket in performance.by_month],
        "best_combination": (
            None if performance.best_combination is None else _bucket(performance.best_combination)
        ),
        "worst_combination": (
            None
            if performance.worst_combination is None
            else _bucket(performance.worst_combination)
        ),
        "signals": [
            {
                "dataset_key": row.dataset_key,
                "signal_id": row.signal_id,
                "setup_identity": row.setup_identity,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "candle_index": row.candle_index,
                "opened_at": row.opened_at.isoformat().replace("+00:00", "Z"),
                "direction": row.direction.value,
                "score_total": row.score_total,
                "labels": [label.value for label in row.labels],
                "combination_key": row.combination_key,
                "outcome_id": row.outcome_id,
                "outcome_status": row.outcome_status.value,
                "reference_close": str(row.reference_close),
                "final_index": row.final_index,
                "final_close": None if row.final_close is None else str(row.final_close),
                "final_return": None if row.final_return is None else str(row.final_return),
                "mfe_return": None if row.mfe_return is None else str(row.mfe_return),
                "mae_return": None if row.mae_return is None else str(row.mae_return),
            }
            for row in report.signals
        ],
    }


def machine_summary(report: BacktestReport) -> bytes:
    """Canonical JSON bytes of the machine-readable backtest summary."""

    return evidence_json(summary_payload(report)).encode("utf-8")
