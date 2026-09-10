"""Phase 19b API: deterministic setup attribution from existing signal facts."""

from smcsignal.analysis.setup_attribution.analyzer import (
    SetupAttributionAnalyzer,
    analyze_setup_attribution,
)
from smcsignal.analysis.setup_attribution.calculation import (
    build_attribution,
    labels_for,
    methodology_version,
)
from smcsignal.analysis.setup_attribution.config import (
    METHODOLOGY_VERSION,
    SetupAttributionConfig,
    load_setup_attribution_config,
)
from smcsignal.analysis.setup_attribution.models import (
    AttributionSnapshot,
    SetupAttribution,
    SetupLabel,
    current_observation,
)

__all__ = [
    "AttributionSnapshot",
    "METHODOLOGY_VERSION",
    "SetupAttribution",
    "SetupAttributionAnalyzer",
    "SetupAttributionConfig",
    "SetupLabel",
    "analyze_setup_attribution",
    "build_attribution",
    "current_observation",
    "labels_for",
    "load_setup_attribution_config",
    "methodology_version",
]
