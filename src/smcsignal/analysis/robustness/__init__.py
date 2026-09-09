"""Phase 21 API: deterministic walk-forward robustness validation."""

from smcsignal.analysis.robustness.analyzer import evaluate_dataset, run_robustness
from smcsignal.analysis.robustness.calculation import (
    build_segment,
    degradation_summary,
    plan_windows,
    segment_status,
    segment_summary,
    split_rows,
    stability_summary,
    window_degradation,
)
from smcsignal.analysis.robustness.config import (
    METHODOLOGY_VERSION,
    RobustnessConfig,
    load_robustness_config,
)
from smcsignal.analysis.robustness.evidence import (
    configuration_artifact,
    configuration_hash,
    machine_summary,
    summary_payload,
)
from smcsignal.analysis.robustness.models import (
    DatasetRobustnessResult,
    DegradationSummary,
    MarketRegime,
    PeriodRange,
    PeriodRow,
    RegimeObservation,
    RobustnessReport,
    SegmentStats,
    SegmentStatus,
    StabilitySummary,
    WalkForwardWindow,
    WindowResult,
)
from smcsignal.analysis.robustness.regime import (
    RegimeAnalyzer,
    analyze_regimes,
    classify_regime,
    efficiency_ratio,
    volatility_ratio,
)
from smcsignal.analysis.robustness.text import render_robustness_text

__all__ = [
    "METHODOLOGY_VERSION",
    "DatasetRobustnessResult",
    "DegradationSummary",
    "MarketRegime",
    "PeriodRange",
    "PeriodRow",
    "RegimeAnalyzer",
    "RegimeObservation",
    "RobustnessConfig",
    "RobustnessReport",
    "SegmentStats",
    "SegmentStatus",
    "StabilitySummary",
    "WalkForwardWindow",
    "WindowResult",
    "analyze_regimes",
    "build_segment",
    "classify_regime",
    "configuration_artifact",
    "configuration_hash",
    "degradation_summary",
    "efficiency_ratio",
    "evaluate_dataset",
    "load_robustness_config",
    "machine_summary",
    "plan_windows",
    "render_robustness_text",
    "run_robustness",
    "segment_status",
    "segment_summary",
    "split_rows",
    "stability_summary",
    "summary_payload",
    "volatility_ratio",
    "window_degradation",
]
