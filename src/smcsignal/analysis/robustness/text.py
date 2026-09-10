"""Deterministic plain-text rendering of robustness reports. No markup, no advice."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.performance.models import PerformanceBucket
from smcsignal.analysis.review.models import reviewed_at_text
from smcsignal.analysis.robustness.config import METHODOLOGY_VERSION
from smcsignal.analysis.robustness.models import RobustnessReport, SegmentStats


def render_robustness_text(
    report: RobustnessReport, *, generated_at: datetime | None = None
) -> str:
    """Render one robustness report as stable plain text; samples always shown.

    The text is a deterministic function of the report and the UTC generation
    instant. It contains no recommendations, forecasts, significance claims,
    or execution language.
    """

    if not isinstance(report, RobustnessReport):
        raise AnalysisInputError("rendering requires a RobustnessReport")
    moment = generated_at if generated_at is not None else datetime.now(UTC)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise AnalysisInputError("generated_at must be a timezone-aware UTC instant")
    lines = [
        f"SMC/ICT signal bot robustness report ({METHODOLOGY_VERSION})",
        "walk-forward validation over the unchanged pipeline; not advice, not optimization",
        f"generated {reviewed_at_text(moment)}",
        f"report {report.report_id}",
        "",
    ]
    for result in report.datasets:
        dataset = result.dataset
        lines.append(
            f"dataset {dataset.symbol} {dataset.timeframe} candles {len(dataset.candles)} "
            f"windows {len(result.windows)}"
        )
    lines.append("")
    lines.extend(_bucket_block("overall", "all", report.overall, 0))
    lines.append("")
    lines.append("windows:")
    for result in report.datasets:
        for window in result.windows:
            for kind, segment in (
                ("development", window.development),
                ("validation", window.validation),
            ):
                lines.extend(
                    _segment_block(
                        f"window {window.window.window_index} {kind} period {segment.label}",
                        segment,
                        1,
                    )
                )
                if kind == "validation":
                    lines.append(
                        f"    degradation win_rate {_optional(window.win_rate_degradation)}"
                    )
                    lines.append(
                        f"    degradation average_final_return "
                        f"{_optional(window.average_final_return_degradation)}"
                    )
    for title, group, buckets in (
        ("by symbol:", "symbol", report.by_symbol),
        ("by timeframe:", "timeframe", report.by_timeframe),
        ("by combination:", "combination", report.by_combination),
    ):
        lines.append("")
        if not buckets:
            lines.append(title + " none")
            continue
        lines.append(title)
        for bucket in buckets:
            lines.extend(_bucket_block(group, bucket.name, bucket, 1))
    lines.append("")
    if not report.by_period:
        lines.append("by period: none")
    else:
        lines.append("by period:")
        for row in report.by_period:
            lines.append(f"  {row.dataset_key}")
            lines.extend(_segment_block(f"period {row.segment.label}", row.segment, 2))
    lines.append("")
    if not report.by_regime:
        lines.append("by regime: none")
    else:
        lines.append("by regime:")
        for segment in report.by_regime:
            lines.extend(_segment_block(f"regime {segment.label}", segment, 1, with_regime=False))
    degradation = report.degradation
    lines.append("")
    lines.append(
        f"degradation: windows {degradation.window_count} "
        f"comparable {degradation.comparable_window_count}"
    )
    lines.append(f"  average_win_rate {_optional(degradation.average_win_rate_degradation)}")
    lines.append(
        f"  average_final_return {_optional(degradation.average_final_return_degradation)}"
    )
    stability = report.stability
    lines.append("")
    lines.append(
        f"stability: segments {stability.validation_segment_count} "
        f"sufficient {stability.sufficient_segment_count} "
        f"positive {stability.positive_segment_count} negative {stability.negative_segment_count}"
    )
    lines.append(f"  win_rate_spread {_optional(stability.win_rate_spread)}")
    lines.append(
        f"  average_final_return_spread {_optional(stability.average_final_return_spread)}"
    )
    lines.append(f"  average_mfe_return_spread {_optional(stability.average_mfe_return_spread)}")
    lines.append(f"  average_mae_return_spread {_optional(stability.average_mae_return_spread)}")
    lines.append(
        f"  best period {stability.best_period or 'none'} "
        f"worst period {stability.worst_period or 'none'}"
    )
    lines.append(f"  status {stability.status.value}")
    return "\n".join(lines).rstrip("\n") + "\n"


def _bucket_block(group: str, name: str, bucket: PerformanceBucket, depth: int) -> list[str]:
    indent = "  " * depth
    return [
        f"{indent}{group} {name}",
        f"{indent}  signals {bucket.total_buy_signals}, open {bucket.open_count}, "
        f"finalized {bucket.finalized_count} "
        f"(win {bucket.win_count}, loss {bucket.loss_count}, flat {bucket.flat_count})",
        f"{indent}  win_rate {_optional(bucket.win_rate)}",
    ]


def _segment_block(
    header: str, segment: SegmentStats, depth: int, *, with_regime: bool = True
) -> list[str]:
    summary = segment.summary
    regime = "unclassified" if segment.regime is None else segment.regime.value
    status = "n/a" if segment.status is None else segment.status.value
    indent = "  " * depth
    lead = f"{header} regime {regime}" if with_regime else header
    return [
        f"{indent}{lead} status {status}",
        f"{indent}  signals {summary.total_buy_signals}, open {summary.open_count}, "
        f"finalized {summary.finalized_count} "
        f"(win {summary.win_count}, loss {summary.loss_count}, flat {summary.flat_count})",
        f"{indent}  win_rate {_optional(summary.win_rate)}",
        f"{indent}  average_final_return {_optional(summary.average_final_return)}",
    ]


def _optional(value: Decimal | None) -> str:
    return "undefined" if value is None else str(value)
