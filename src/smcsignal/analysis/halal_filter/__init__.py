"""Phase 14 API: configuration-driven halal registry enforcement, not a fatwa."""

from smcsignal.analysis.halal_filter.analyzer import HalalFilterAnalyzer, analyze_halal
from smcsignal.analysis.halal_filter.classification import AssetClassification, classify_asset
from smcsignal.analysis.halal_filter.config import (
    FilterMode,
    HalalFilterConfig,
    load_halal_filter_config,
)
from smcsignal.analysis.halal_filter.models import HalalDecision, HalalSnapshot

__all__ = [
    "AssetClassification",
    "FilterMode",
    "HalalDecision",
    "HalalFilterAnalyzer",
    "HalalFilterConfig",
    "HalalSnapshot",
    "analyze_halal",
    "classify_asset",
    "load_halal_filter_config",
]
