"""Phase 20 API: deterministic historical replay and backtesting foundation."""

from smcsignal.analysis.backtest.calculation import (
    effective_mtf_config,
    replay_rows,
    series_key_for,
    signal_rows,
)
from smcsignal.analysis.backtest.config import (
    METHODOLOGY_VERSION,
    BacktestConfig,
    load_backtest_config,
    load_backtest_configuration,
)
from smcsignal.analysis.backtest.evidence import (
    candle_prefix_seed,
    configuration_artifact,
    configuration_hash,
    machine_summary,
    summary_payload,
)
from smcsignal.analysis.backtest.models import (
    BacktestConfiguration,
    BacktestReport,
    BacktestSignalResult,
    ReplayDataset,
    ReplayResult,
    ReplayStep,
)
from smcsignal.analysis.backtest.replay import (
    HistoricalReplay,
    replay_history,
    run_backtest,
)
from smcsignal.analysis.backtest.text import render_backtest_text

__all__ = [
    "METHODOLOGY_VERSION",
    "BacktestConfig",
    "BacktestConfiguration",
    "BacktestReport",
    "BacktestSignalResult",
    "HistoricalReplay",
    "ReplayDataset",
    "ReplayResult",
    "ReplayStep",
    "candle_prefix_seed",
    "configuration_artifact",
    "configuration_hash",
    "effective_mtf_config",
    "load_backtest_config",
    "load_backtest_configuration",
    "machine_summary",
    "replay_history",
    "replay_rows",
    "render_backtest_text",
    "run_backtest",
    "series_key_for",
    "signal_rows",
    "summary_payload",
]
