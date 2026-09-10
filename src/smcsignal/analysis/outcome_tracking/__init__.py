"""Phase 18 API: BUY_SIGNAL outcome analytics over existing spot signal frames."""

from smcsignal.analysis.outcome_tracking.analyzer import (
    OutcomeTrackingAnalyzer,
    analyze_outcome_tracking,
)
from smcsignal.analysis.outcome_tracking.calculation import (
    advance_outcome,
    aggregate,
    classify_outcome,
    final_difference,
    final_return,
    improved_high,
    improved_low,
    mae_return,
    mfe_return,
    open_outcome,
)
from smcsignal.analysis.outcome_tracking.config import (
    DEFAULT_HORIZON_BARS,
    MAX_HORIZON_BARS,
    METHODOLOGY_VERSION,
    OutcomeTrackingConfig,
    load_outcome_tracking_config,
)
from smcsignal.analysis.outcome_tracking.models import (
    AnalyticsSummary,
    OutcomeSnapshot,
    OutcomeStatus,
    SignalOutcome,
    current_observation,
)

__all__ = [
    "AnalyticsSummary",
    "DEFAULT_HORIZON_BARS",
    "MAX_HORIZON_BARS",
    "METHODOLOGY_VERSION",
    "OutcomeSnapshot",
    "OutcomeStatus",
    "OutcomeTrackingAnalyzer",
    "OutcomeTrackingConfig",
    "SignalOutcome",
    "advance_outcome",
    "aggregate",
    "analyze_outcome_tracking",
    "classify_outcome",
    "current_observation",
    "final_difference",
    "final_return",
    "improved_high",
    "improved_low",
    "load_outcome_tracking_config",
    "mae_return",
    "mfe_return",
    "open_outcome",
]
