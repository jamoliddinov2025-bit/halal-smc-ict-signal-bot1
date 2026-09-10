"""Phase 22 API: deterministic Strategy Intelligence research reporting.

A consumer-only descriptive layer over the validated Phase 21
``RobustnessReport``. It describes how published, out-of-sample spot BUY_SIGNAL
facts distribute across signal-time strategy profiles, with deterministic
winner/loser patterns, strength/weakness diagnostics, and research rankings
gated by a minimum finalized sample. Research only: no optimization, parameter
or threshold tuning, automatic strategy selection, setup enabling/disabling,
signal veto, regime re-detection, feedback into signal generation,
self-modification, live trading, or advice.
"""

from smcsignal.analysis.intelligence.analyzer import analyze_report, run_intelligence
from smcsignal.analysis.intelligence.calculation import build_cell, diagnose, rank_cells
from smcsignal.analysis.intelligence.config import (
    METHODOLOGY_VERSION,
    IntelligenceConfig,
    load_intelligence_config,
)
from smcsignal.analysis.intelligence.evidence import (
    configuration_artifact,
    configuration_hash,
    intelligence_identity,
    machine_summary,
    summary_payload,
)
from smcsignal.analysis.intelligence.models import (
    DiagnosticLabel,
    IntelligenceCell,
    IntelligenceDimension,
    IntelligencePattern,
    IntelligenceReport,
)
from smcsignal.analysis.intelligence.text import render_intelligence_text

__all__ = [
    "METHODOLOGY_VERSION",
    "DiagnosticLabel",
    "IntelligenceCell",
    "IntelligenceConfig",
    "IntelligenceDimension",
    "IntelligencePattern",
    "IntelligenceReport",
    "analyze_report",
    "build_cell",
    "configuration_artifact",
    "configuration_hash",
    "diagnose",
    "intelligence_identity",
    "load_intelligence_config",
    "machine_summary",
    "rank_cells",
    "render_intelligence_text",
    "run_intelligence",
    "summary_payload",
]
