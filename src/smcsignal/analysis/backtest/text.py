"""Deterministic plain-text rendering of backtest summaries. No markup, no advice."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from smcsignal.analysis.backtest.config import METHODOLOGY_VERSION
from smcsignal.analysis.backtest.models import BacktestReport
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.performance.models import PerformanceBucket
from smcsignal.analysis.review.models import reviewed_at_text


def render_backtest_text(report: BacktestReport, *, generated_at: datetime | None = None) -> str:
    """Render one backtest report as stable plain text; samples always shown.

    The text is a deterministic function of the report and the UTC generation
    instant. It renders the composed Phase 19c performance buckets and the
    replayed dataset identities. It contains no recommendations, forecasts,
    orders, or execution language.
    """

    if not isinstance(report, BacktestReport):
        raise AnalysisInputError("rendering requires a BacktestReport")
    moment = generated_at if generated_at is not None else datetime.now(UTC)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise AnalysisInputError("generated_at must be a timezone-aware UTC instant")
    performance = report.performance
    overall = performance.overall
    lines = [
        f"SMC/ICT signal bot backtest summary ({METHODOLOGY_VERSION})",
        "historical replay through the unchanged pipeline; not advice, not execution",
        f"generated {reviewed_at_text(moment)}",
        f"backtest {report.backtest_id}",
        "",
    ]
    for replay in report.replays:
        dataset = replay.dataset
        higher = ", ".join(
            f"{timeframe} {len(dataset.higher_candles[timeframe])}"
            for timeframe in sorted(dataset.higher_candles)
        )
        suffix = f"; higher {higher}" if higher else "; higher none"
        lines.append(
            f"dataset {dataset.symbol} {dataset.timeframe} candles {len(dataset.candles)}{suffix}"
        )
    lines.append(f"replays {len(report.replays)}")
    lines.append("")
    lines.append(_bucket_line("overall", "all", overall))
    lines.append(f"  win_rate {_optional(overall.win_rate)}")
    lines.append(f"  final_return_sum {overall.final_return_sum}")
    lines.append(f"  mfe_return_sum {overall.mfe_return_sum}")
    lines.append(f"  mae_return_sum {overall.mae_return_sum}")
    lines.append(f"  average_final_return {_optional(overall.average_final_return)}")
    lines.append(f"  average_mfe_return {_optional(overall.average_mfe_return)}")
    lines.append(f"  average_mae_return {_optional(overall.average_mae_return)}")
    lines.append(
        "  best combination "
        + ("none" if performance.best_combination is None else performance.best_combination.name)
    )
    lines.append(
        "  worst combination "
        + ("none" if performance.worst_combination is None else performance.worst_combination.name)
    )
    for group, buckets in (
        ("symbol", performance.by_symbol),
        ("timeframe", performance.by_timeframe),
        ("label", performance.by_label),
        ("combination", performance.by_combination),
        ("month", performance.by_month),
    ):
        lines.append("")
        if not buckets:
            lines.append(f"by {group}: none")
            continue
        lines.append(f"by {group}:")
        for bucket in buckets:
            lines.append("  " + _bucket_line(group, bucket.name, bucket))
            lines.append(f"    win_rate {_optional(bucket.win_rate)}")
            lines.append(f"    average_final_return {_optional(bucket.average_final_return)}")
    return "\n".join(lines).rstrip("\n") + "\n"


def _bucket_line(group: str, name: str, bucket: PerformanceBucket) -> str:
    return (
        f"{group} {name}: signals {bucket.total_buy_signals}, "
        f"open {bucket.open_count}, finalized {bucket.finalized_count} "
        f"(win {bucket.win_count}, loss {bucket.loss_count}, flat {bucket.flat_count})"
    )


def _optional(value: Decimal | None) -> str:
    return "undefined" if value is None else str(value)
