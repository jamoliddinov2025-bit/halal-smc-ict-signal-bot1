"""Public analysis API through Phase 4: structure, liquidity pools, and sweeps."""

from smcsignal.analysis.config import AnalysisConfig, load_analysis_config
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisError, AnalysisInputError
from smcsignal.analysis.liquidity import (
    InvalidationReason,
    LiquidityAnalyzer,
    LiquidityConfig,
    LiquidityKind,
    LiquidityPool,
    LiquiditySide,
    LiquiditySnapshot,
    ObservedCandle,
    PoolStatus,
    StructureContext,
    SweepEvent,
    SwingEvidence,
    analyze_liquidity,
    candle_close_time,
    evidence_json,
    load_liquidity_config,
)
from smcsignal.analysis.models import (
    AnalysisSnapshot,
    StructureEvent,
    StructureEventKind,
    Swing,
    SwingKind,
    TrendDirection,
    TrendState,
)
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    ProvenancedEvidence,
    SeriesProvenance,
)
from smcsignal.analysis.structure import MarketStructureAnalyzer, analyze
from smcsignal.analysis.swings import SwingDetector, detect_swings
from smcsignal.analysis.trend import classify_trend

__all__ = [
    "InvalidationReason",
    "LiquidityAnalyzer",
    "LiquidityConfig",
    "LiquidityKind",
    "LiquidityPool",
    "LiquiditySide",
    "LiquiditySnapshot",
    "ObservedCandle",
    "PoolStatus",
    "StructureContext",
    "SweepEvent",
    "SwingEvidence",
    "analyze_liquidity",
    "candle_close_time",
    "evidence_json",
    "load_liquidity_config",
    "AnalysisConfig",
    "AnalysisConfigurationError",
    "AnalysisError",
    "AnalysisInputError",
    "AnalysisSnapshot",
    "CandleReference",
    "EvidenceProvenance",
    "EvidenceReference",
    "ProvenancedEvidence",
    "SeriesProvenance",
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
