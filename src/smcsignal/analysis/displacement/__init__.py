"""Phase 5 public API: objective, single-candle displacement evidence only."""

from smcsignal.analysis.displacement.analyzer import DisplacementAnalyzer, analyze_displacement
from smcsignal.analysis.displacement.config import DisplacementConfig, load_displacement_config
from smcsignal.analysis.displacement.models import (
    ATRReference,
    DisplacementEvent,
    DisplacementMetrics,
    DisplacementSnapshot,
    SweepContext,
)

__all__ = [
    "ATRReference",
    "DisplacementAnalyzer",
    "DisplacementConfig",
    "DisplacementEvent",
    "DisplacementMetrics",
    "DisplacementSnapshot",
    "SweepContext",
    "analyze_displacement",
    "load_displacement_config",
]
