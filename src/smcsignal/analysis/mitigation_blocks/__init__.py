"""Phase 11 API: immutable first-interaction mitigation evidence only."""

from smcsignal.analysis.mitigation_blocks.analyzer import (
    MitigationBlockAnalyzer,
    analyze_mitigation_blocks,
)
from smcsignal.analysis.mitigation_blocks.calculation import MitigationDirection
from smcsignal.analysis.mitigation_blocks.config import (
    InteractionBasis,
    MitigationBlockConfig,
    load_mitigation_block_config,
)
from smcsignal.analysis.mitigation_blocks.models import (
    MitigationBlock,
    MitigationEvidence,
    MitigationSnapshot,
)

__all__ = [
    "InteractionBasis",
    "MitigationBlock",
    "MitigationBlockAnalyzer",
    "MitigationBlockConfig",
    "MitigationDirection",
    "MitigationEvidence",
    "MitigationSnapshot",
    "analyze_mitigation_blocks",
    "load_mitigation_block_config",
]
