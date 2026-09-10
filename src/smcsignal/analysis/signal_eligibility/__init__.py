"""Phase 16 API: eligibility from existing nested facts, not trade sides."""

from smcsignal.analysis.signal_eligibility.analyzer import (
    SignalEligibilityAnalyzer,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_eligibility.config import (
    ConflictPolicy,
    SignalEligibilityConfig,
    load_signal_eligibility_config,
)
from smcsignal.analysis.signal_eligibility.models import (
    EligibilityDecision,
    EligibilityReason,
    EligibilitySnapshot,
    EligibilityStatus,
    MarketBias,
    SignalEligibility,
)

__all__ = [
    "ConflictPolicy",
    "EligibilityDecision",
    "EligibilityReason",
    "EligibilitySnapshot",
    "EligibilityStatus",
    "MarketBias",
    "SignalEligibility",
    "SignalEligibilityAnalyzer",
    "SignalEligibilityConfig",
    "analyze_signal_eligibility",
    "load_signal_eligibility_config",
]
