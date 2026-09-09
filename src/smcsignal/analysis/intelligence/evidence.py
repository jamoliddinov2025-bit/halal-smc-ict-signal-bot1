"""Deterministic configuration artifacts, identities, and machine summaries."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from smcsignal.analysis.intelligence.config import METHODOLOGY_VERSION, IntelligenceConfig
from smcsignal.analysis.intelligence.models import IntelligenceCell, IntelligenceReport
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest, evidence_json

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from smcsignal.analysis.backtest.models import BacktestSignalResult

    RegimeMap = Mapping[str, str]


def configuration_artifact(config: IntelligenceConfig) -> bytes:
    """Frozen methodology artifact; the research-only role is declared."""

    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "role": "strategy_intelligence_research_only",
            "optimization": False,
            "parameter_selection": False,
            "parameter_tuning": False,
            "automatic_strategy_selection": False,
            "threshold_tuning": False,
            "self_modification": False,
            "feedback_into_signal_generation": False,
            "automatic_setup_enabling": False,
            "automatic_setup_disabling": False,
            "signal_veto": False,
            "signal_generation": False,
            "regime_re_detection": False,
            "live_trading": False,
            "execution": False,
            "advice": False,
            "phase_23_optimization": False,
        }
    )


def configuration_hash(config: IntelligenceConfig) -> str:
    """SHA-256 of the frozen intelligence artifact."""

    return sha256(configuration_artifact(config)).hexdigest()


def intelligence_identity(
    settings: IntelligenceConfig,
    rows: Sequence[BacktestSignalResult],
    regimes: RegimeMap,
) -> str:
    """Stable identity from the configuration and the exact published row facts.

    The digest covers the configuration hash, the sorted series keys, and, for
    every validation signal in deterministic order, its signal-time facts and
    its Phase 21 regime label and outcome status. It deliberately does NOT
    embed the Phase 21 ``report_id`` or any walk-forward window identity, so a
    future tail whose causal regime annotation legitimately differs cannot
    change already-observed intelligence facts. Identical inputs always yield
    the identical report id.
    """

    series_keys = tuple(sorted({row.dataset_key for row in rows}))
    ordered_rows = sorted(rows, key=lambda row: row.signal_id)
    payload = {
        "methodology": METHODOLOGY_VERSION,
        "configuration_hash": configuration_hash(settings),
        "series_keys": list(series_keys),
        "signals": [
            {
                "signal_id": row.signal_id,
                "dataset_key": row.dataset_key,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "opened_at": row.opened_at.isoformat().replace("+00:00", "Z"),
                "combination_key": row.combination_key,
                "regime": regimes[row.signal_id],
                "outcome_status": row.outcome_status.value,
                "final_return": None if row.final_return is None else str(row.final_return),
            }
            for row in ordered_rows
        ],
    }
    return f"intelligence-report:{digest(payload)}"


def _cell_payload(cell: IntelligenceCell) -> dict[str, object]:
    return {
        "dimension": cell.dimension.value,
        "name": cell.name,
        "total_buy_signals": cell.total_buy_signals,
        "open_count": cell.open_count,
        "win_count": cell.win_count,
        "loss_count": cell.loss_count,
        "flat_count": cell.flat_count,
        "finalized_count": cell.finalized_count,
        "final_return_sum": str(cell.final_return_sum),
        "mfe_return_sum": str(cell.mfe_return_sum),
        "mae_return_sum": str(cell.mae_return_sum),
        "win_rate": None if cell.win_rate is None else str(cell.win_rate),
        "average_final_return": (
            None if cell.average_final_return is None else str(cell.average_final_return)
        ),
        "average_mfe_return": (
            None if cell.average_mfe_return is None else str(cell.average_mfe_return)
        ),
        "average_mae_return": (
            None if cell.average_mae_return is None else str(cell.average_mae_return)
        ),
        "sufficient_sample": cell.sufficient,
        "pattern": cell.pattern.value,
        "diagnostic": cell.diagnostic.value,
        "rank": cell.rank,
    }


def summary_payload(report: IntelligenceReport) -> dict[str, object]:
    """Deterministic machine-readable summary as canonical JSON data.

    Every value is copied from the report's exact Decimal statistics; the
    payload is observational research output, not advice or a decision.
    """

    return {
        "methodology": METHODOLOGY_VERSION,
        "report_id": report.report_id,
        "configuration_hash": configuration_hash(report.settings),
        "series_keys": list(report.series_keys),
        "role": "strategy_intelligence_research_only",
        "optimization": False,
        "parameter_selection": False,
        "parameter_tuning": False,
        "automatic_strategy_selection": False,
        "threshold_tuning": False,
        "self_modification": False,
        "feedback_into_signal_generation": False,
        "automatic_setup_enabling": False,
        "automatic_setup_disabling": False,
        "signal_veto": False,
        "regime_re_detection": False,
        "advice": False,
        "phase_23_optimization": False,
        "overall": _cell_payload(report.overall),
        "by_setup": [_cell_payload(cell) for cell in report.by_setup],
        "by_symbol": [_cell_payload(cell) for cell in report.by_symbol],
        "by_timeframe": [_cell_payload(cell) for cell in report.by_timeframe],
        "by_month": [_cell_payload(cell) for cell in report.by_month],
        "by_regime": [_cell_payload(cell) for cell in report.by_regime],
    }


def machine_summary(report: IntelligenceReport) -> bytes:
    """Canonical JSON bytes of the machine-readable intelligence summary."""

    return evidence_json(summary_payload(report)).encode("utf-8")
