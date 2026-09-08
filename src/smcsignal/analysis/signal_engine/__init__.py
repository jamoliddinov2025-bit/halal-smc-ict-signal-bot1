"""Phase 17 API: spot signal facts from existing eligibility, not execution."""

from smcsignal.analysis.signal_engine.analyzer import (
    SignalEngineAnalyzer,
    analyze_signal_engine,
)
from smcsignal.analysis.signal_engine.calculation import (
    build_signal_snapshot,
    direction_for,
    status_for,
)
from smcsignal.analysis.signal_engine.config import (
    DuplicatePolicy,
    SignalEngineConfig,
    load_signal_engine_config,
)
from smcsignal.analysis.signal_engine.models import (
    Signal,
    SignalCandidate,
    SignalDirection,
    SignalReason,
    SignalSnapshot,
    SignalStatus,
)

__all__ = [
    "DuplicatePolicy",
    "Signal",
    "SignalCandidate",
    "SignalDirection",
    "SignalEngineAnalyzer",
    "SignalEngineConfig",
    "SignalReason",
    "SignalSnapshot",
    "SignalStatus",
    "analyze_signal_engine",
    "build_signal_snapshot",
    "direction_for",
    "load_signal_engine_config",
    "status_for",
]
