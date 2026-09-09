"""Strict backtest-replay-v1 invariants; historical research only, never execution."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

from smcsignal.analysis.errors import AnalysisConfigurationError

if TYPE_CHECKING:
    from smcsignal.analysis.backtest.models import BacktestConfiguration

METHODOLOGY_VERSION = "backtest-replay-v1"


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Declare frozen backtest invariants.

    A backtest is a deterministic chronological replay of one declared
    historical dataset through the unchanged Phase 3–19 pipeline. Every
    detection, signal, outcome, attribution, and performance rule is reused;
    the layer only orchestrates and reports. The only setting is that the
    layer is enabled. No order, position, sizing, fee, slippage, or
    self-modification setting exists.
    """

    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict backtest-replay-v1")


def load_backtest_config(path: str | Path) -> BacktestConfig:
    """Load the exact approved [backtest] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("backtest")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load backtest configuration: {exc}") from exc
    names = {field.name for field in fields(BacktestConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[backtest] must contain exactly: " + ", ".join(sorted(names))
        )
    return BacktestConfig(**table)


def load_backtest_configuration(path: str | Path) -> BacktestConfiguration:
    """Load one full replay configuration through the existing strict loaders.

    Every table is read by its existing Phase 3-19 loader, so a backtest
    configuration is exactly the already-approved pipeline configuration.
    The [analysis] table may be omitted (the analyzer default is used);
    every other table, including [backtest], must be present and exact.
    The bundle itself cross-validates the threshold invariants.
    """

    from smcsignal.analysis.backtest.models import BacktestConfiguration
    from smcsignal.analysis.config import load_analysis_config
    from smcsignal.analysis.displacement import load_displacement_config
    from smcsignal.analysis.fvg import load_fvg_config
    from smcsignal.analysis.halal_filter import load_halal_filter_config
    from smcsignal.analysis.liquidity import load_liquidity_config
    from smcsignal.analysis.mtf import load_mtf_config
    from smcsignal.analysis.order_blocks import load_order_block_config
    from smcsignal.analysis.ote import load_ote_config
    from smcsignal.analysis.outcome_tracking import load_outcome_tracking_config
    from smcsignal.analysis.performance import load_performance_config
    from smcsignal.analysis.premium_discount import load_pd_config
    from smcsignal.analysis.setup_attribution import load_setup_attribution_config
    from smcsignal.analysis.setup_quality import load_setup_quality_config
    from smcsignal.analysis.signal_eligibility import load_signal_eligibility_config
    from smcsignal.analysis.signal_engine import load_signal_engine_config

    try:
        with Path(path).open("rb") as stream:
            tables = set(tomllib.load(stream))
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load backtest configuration: {exc}") from exc
    required = {
        "backtest",
        "liquidity",
        "displacement",
        "fvg",
        "order_blocks",
        "premium_discount",
        "ote",
        "mtf",
        "halal_filter",
        "setup_quality",
        "signal_eligibility",
        "signal_engine",
        "outcome_tracking",
        "setup_attribution",
        "performance",
    }
    missing = sorted(required - tables)
    if missing:
        raise AnalysisConfigurationError(
            "a backtest configuration requires the missing tables: " + ", ".join(missing)
        )
    analysis = load_analysis_config(path) if "analysis" in tables else None
    return BacktestConfiguration(
        backtest=load_backtest_config(path),
        analysis=analysis,
        liquidity=load_liquidity_config(path),
        displacement=load_displacement_config(path),
        fvg=load_fvg_config(path),
        order_blocks=load_order_block_config(path),
        premium_discount=load_pd_config(path),
        ote=load_ote_config(path),
        mtf=load_mtf_config(path),
        halal_filter=load_halal_filter_config(path),
        setup_quality=load_setup_quality_config(path),
        signal_eligibility=load_signal_eligibility_config(path),
        signal_engine=load_signal_engine_config(path),
        outcome_tracking=load_outcome_tracking_config(path),
        setup_attribution=load_setup_attribution_config(path),
        performance=load_performance_config(path),
    )
