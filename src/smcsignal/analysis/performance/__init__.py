"""Phase 19c API: descriptive performance analytics over published records."""

from smcsignal.analysis.performance.analyzer import (
    analyze_performance,
    configuration_hash,
)
from smcsignal.analysis.performance.calculation import (
    bucket_for,
    latest_outcomes,
    month_of,
    rank_combinations,
)
from smcsignal.analysis.performance.config import (
    DEFAULT_MINIMUM_FINALIZED_FOR_RANKING,
    MAXIMUM_FINALIZED_FOR_RANKING,
    METHODOLOGY_VERSION,
    PerformanceConfig,
    load_performance_config,
)
from smcsignal.analysis.performance.models import (
    GROUPS,
    NO_LABELS_KEY,
    PerformanceBucket,
    PerformanceReport,
)

__all__ = [
    "DEFAULT_MINIMUM_FINALIZED_FOR_RANKING",
    "GROUPS",
    "MAXIMUM_FINALIZED_FOR_RANKING",
    "METHODOLOGY_VERSION",
    "NO_LABELS_KEY",
    "PerformanceBucket",
    "PerformanceConfig",
    "PerformanceReport",
    "analyze_performance",
    "bucket_for",
    "configuration_hash",
    "latest_outcomes",
    "load_performance_config",
    "month_of",
    "rank_combinations",
]
