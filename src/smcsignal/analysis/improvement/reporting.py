"""Deterministic machine- and human-readable Phase 23B comparison output.

Rendering is pure text/dict formatting over an already-built comparison; it
never recomputes a metric, never rounds a Decimal, and never adds a claim.
"""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.results import (
    CandidateComparisonReport,
    ComparisonRow,
)


def _format(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return format(value, "f")


def machine_comparison(report: CandidateComparisonReport) -> dict[str, object]:
    """Return a deterministic, JSON-serializable machine-readable comparison.

    Decimals are rendered as exact plain strings (never rounded) so the result
    can be represented losslessly in JSON; differences remain exact.
    """

    if not isinstance(report, CandidateComparisonReport):
        raise AnalysisInputError("machine_comparison requires a CandidateComparisonReport")
    rows: list[dict[str, object]] = []
    for row in sorted(report.rows, key=lambda r: (r.dimension, r.group, r.metric)):
        rows.append(_row_payload(row))
    return {
        "methodology": "improvement-eval-v1",
        "kind": "candidate-vs-baseline-comparison",
        "comparison_id": report.comparison_id,
        "baseline_hash": report.baseline_hash,
        "candidate_id": report.candidate_id,
        "baseline_run_id": report.baseline_run_id,
        "candidate_run_id": report.candidate_run_id,
        "protocol_identity": report.protocol_identity,
        "minimum_finalized_for_comparison": report.minimum_finalized_for_comparison,
        "limitations": list(report.limitations),
        "rows": rows,
    }


def _row_payload(row: ComparisonRow) -> dict[str, object]:
    return {
        "dimension": row.dimension,
        "group": row.group,
        "metric": row.metric,
        "candidate_value": _format(row.candidate_value),
        "baseline_value": _format(row.baseline_value),
        "difference": _format(row.difference),
        "candidate_sample": row.candidate_sample,
        "baseline_sample": row.baseline_sample,
        "conclusive": row.conclusive,
    }


def human_comparison(report: CandidateComparisonReport) -> str:
    """Return a deterministic human-readable rendering of a comparison."""

    if not isinstance(report, CandidateComparisonReport):
        raise AnalysisInputError("human_comparison requires a CandidateComparisonReport")
    lines: list[str] = []
    lines.append("Phase 23B candidate-versus-baseline comparison (evidence only)")
    lines.append(f"comparison id : {report.comparison_id}")
    lines.append(f"candidate id  : {report.candidate_id}")
    lines.append(f"baseline hash : {report.baseline_hash}")
    lines.append(f"candidate run : {report.candidate_run_id}")
    lines.append(f"baseline run  : {report.baseline_run_id}")
    lines.append(f"protocol      : {report.protocol_identity}")
    lines.append(f"comparison minimum finalized: {report.minimum_finalized_for_comparison}")
    lines.append("")
    if not report.rows:
        lines.append("(no comparable evidence rows)")
    last_dimension: str | None = None
    for row in sorted(report.rows, key=lambda r: (r.dimension, r.group, r.metric)):
        if row.dimension != last_dimension:
            lines.append("")
            lines.append(f"[{row.dimension}]")
            last_dimension = row.dimension
        conclusive = "conclusive" if row.conclusive else "inconclusive"
        marker = "!" if row.conclusive else " "
        lines.append(
            f" {marker} {row.group}.{row.metric}: "
            f"candidate={_format(row.candidate_value)} "
            f"baseline={_format(row.baseline_value)} "
            f"delta(candidate-baseline)={_format(row.difference)} "
            f"n_candidate={row.candidate_sample} n_baseline={row.baseline_sample} "
            f"({conclusive})"
        )
    lines.append("")
    lines.append("Limitations")
    for limitation in report.limitations:
        lines.append(f" - {limitation}")
    return "\n".join(lines)
