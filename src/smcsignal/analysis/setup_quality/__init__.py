"""Phase 15 API: integer setup quality from existing nested evidence, not a signal."""

from smcsignal.analysis.setup_quality.analyzer import SetupQualityAnalyzer, analyze_setup_quality
from smcsignal.analysis.setup_quality.config import (
    DEFAULT_PUBLISH_THRESHOLD,
    SetupQualityConfig,
    load_setup_quality_config,
)
from smcsignal.analysis.setup_quality.models import (
    WEIGHTS,
    ScoreBreakdown,
    ScoreComponent,
    ScoreSnapshot,
    SetupQualityScore,
)

__all__ = [
    "DEFAULT_PUBLISH_THRESHOLD",
    "WEIGHTS",
    "ScoreBreakdown",
    "ScoreComponent",
    "ScoreSnapshot",
    "SetupQualityAnalyzer",
    "SetupQualityConfig",
    "SetupQualityScore",
    "analyze_setup_quality",
    "load_setup_quality_config",
]
