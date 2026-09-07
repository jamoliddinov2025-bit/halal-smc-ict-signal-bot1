"""Phase 3 public API: confirmed swings, historical trend, and BOS/CHoCH only."""

from smcsignal.analysis.config import AnalysisConfig, load_analysis_config
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisError, AnalysisInputError
from smcsignal.analysis.models import (
    AnalysisSnapshot,
    StructureEvent,
    StructureEventKind,
    Swing,
    SwingKind,
    TrendDirection,
    TrendState,
)
from smcsignal.analysis.structure import MarketStructureAnalyzer, analyze
from smcsignal.analysis.swings import SwingDetector, detect_swings
from smcsignal.analysis.trend import classify_trend

__all__ = [
    "AnalysisConfig",
    "AnalysisConfigurationError",
    "AnalysisError",
    "AnalysisInputError",
    "AnalysisSnapshot",
    "MarketStructureAnalyzer",
    "StructureEvent",
    "StructureEventKind",
    "Swing",
    "SwingDetector",
    "SwingKind",
    "TrendDirection",
    "TrendState",
    "analyze",
    "classify_trend",
    "detect_swings",
    "load_analysis_config",
]
