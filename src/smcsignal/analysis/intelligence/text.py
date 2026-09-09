"""Deterministic human-readable rendering of a strategy-intelligence report."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.intelligence.models import (
    DiagnosticLabel,
    IntelligenceCell,
    IntelligenceDimension,
    IntelligenceReport,
)

_ROLE = (
    "Observational research only over published Phase 21 validation facts. No "
    "optimization, parameter or threshold tuning, automatic strategy "
    "selection, enabling or disabling of setups, signal veto, regime "
    "re-detection, feedback into signal generation, self-modification, live "
    "trading, or advice. Deterministic ranks describe published records and "
    "select nothing. Phase 23 optimization is not approved and is never run."
)


def _ratio(value: Decimal | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return format(value.quantize(Decimal("0." + "0" * digits)), "f")


def _cell_line(cell: IntelligenceCell) -> str:
    return (
        f"  {cell.name}: signals={cell.total_buy_signals} "
        f"finalized={cell.finalized_count} open={cell.open_count} "
        f"wins={cell.win_count} losses={cell.loss_count} flat={cell.flat_count} "
        f"win_rate={_ratio(cell.win_rate)} avg_final={_ratio(cell.average_final_return)} "
        f"pattern={cell.pattern.value} diagnostic={cell.diagnostic.value} "
        f"rank={'n/a' if cell.rank is None else cell.rank}"
    )


def _heading(dimension: IntelligenceDimension) -> str:
    return {
        IntelligenceDimension.SETUP: "Setup intelligence (by setup combination)",
        IntelligenceDimension.SYMBOL: "Conditional by symbol",
        IntelligenceDimension.TIMEFRAME: "Conditional by timeframe",
        IntelligenceDimension.MONTH: "Conditional by month",
        IntelligenceDimension.REGIME: "Conditional by market regime",
    }[dimension]


def render_intelligence_text(report: IntelligenceReport) -> str:
    """Render one deterministic human-readable intelligence research report."""

    overall = report.overall
    lines = [
        "Strategy Intelligence report",
        f"Report id: {report.report_id}",
        f"Series: {', '.join(report.series_keys)}",
        "Population: Phase 21 validation rows, each described exactly once.",
        _cell_line(overall),
    ]
    for cells in (
        report.by_setup,
        report.by_symbol,
        report.by_timeframe,
        report.by_month,
        report.by_regime,
    ):
        if not cells:
            continue
        lines.append(_heading(cells[0].dimension))
        for cell in cells:
            lines.append(_cell_line(cell))
        strengths = sum(cell.diagnostic is DiagnosticLabel.STRENGTH for cell in cells)
        weaknesses = sum(cell.diagnostic is DiagnosticLabel.WEAKNESS for cell in cells)
        lines.append(f"  strength diagnostics={strengths} weakness diagnostics={weaknesses}")
    lines.append(_ROLE)
    return "\n".join(lines) + "\n"
