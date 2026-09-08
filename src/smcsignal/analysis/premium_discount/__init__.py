"""Phase 8 API: confirmed local ranges, exact equilibrium, and immutable PD context."""

from smcsignal.analysis.premium_discount.analyzer import PDAnalyzer, analyze_pd
from smcsignal.analysis.premium_discount.calculation import (
    PDArrayKind,
    PDClassification,
    RangeStatus,
)
from smcsignal.analysis.premium_discount.config import PDConfig, load_pd_config
from smcsignal.analysis.premium_discount.models import (
    DealingRange,
    Equilibrium,
    PDArrayContext,
    PDContextReference,
    PDSnapshot,
)

__all__ = [
    "DealingRange",
    "Equilibrium",
    "PDAnalyzer",
    "PDArrayContext",
    "PDArrayKind",
    "PDClassification",
    "PDConfig",
    "PDContextReference",
    "PDSnapshot",
    "RangeStatus",
    "analyze_pd",
    "load_pd_config",
]
