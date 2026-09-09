"""Synthetic Phase 22 fixtures over the Phase 21 robustness layer; never duplicated."""

from __future__ import annotations

from decimal import Decimal
from functools import cache

from smcsignal.analysis.intelligence import IntelligenceConfig
from smcsignal.analysis.robustness import RobustnessReport, run_robustness
from tests.robustness.helpers import configuration, rich_dataset, robustness

__all__ = [
    "intelligence",
    "report",
    "rich_report",
]


def intelligence(
    *,
    minimum_finalized_for_diagnosis: int = 20,
    minimum_finalized_for_ranking: int = 20,
    winner_win_rate_floor: str = "0.60",
    loser_win_rate_ceiling: str = "0.40",
) -> IntelligenceConfig:
    """A configurable intelligence gate; defaults mirror the frozen defaults."""

    return IntelligenceConfig(
        minimum_finalized_for_diagnosis=minimum_finalized_for_diagnosis,
        minimum_finalized_for_ranking=minimum_finalized_for_ranking,
        winner_win_rate_floor=Decimal(winner_win_rate_floor),
        loser_win_rate_ceiling=Decimal(loser_win_rate_ceiling),
    )


@cache
def rich_report(symbols: tuple[str, ...] = ("BTCUSDT",)) -> RobustnessReport:
    """A deterministic Phase 21 report over one or more rich synthetic datasets."""
    return run_robustness(
        [rich_dataset(symbol=symbol) for symbol in symbols], configuration(), robustness()
    )


def report() -> RobustnessReport:
    """The canonical single-dataset Phase 21 report used across Phase 22 tests."""
    return rich_report(("BTCUSDT",))
