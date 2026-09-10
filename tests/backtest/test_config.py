"""Backtest configuration, TOML loaders, and bundle cross-validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from smcsignal.analysis.backtest import (
    BacktestConfig,
    BacktestConfiguration,
    load_backtest_config,
    load_backtest_configuration,
)
from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.liquidity import LiquidityConfig
from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_engine import SignalEngineConfig
from tests.backtest.helpers import configuration

FULL_TOML = """
[backtest]
enabled = true

[analysis]
fractal_length = 3

[liquidity]
price_unit = "USDT"
equal_tolerance_bps = "0"

[displacement]
atr_period = 3
min_body_atr = "1.0"
min_range_atr = "1.5"
bullish_close_min = "0.70"
bearish_close_max = "0.30"
atr_floor = "0"
sweep_lookback_bars = 20

[fvg]
min_gap_size = "0"
require_displacement = false

[order_blocks]
max_candidate_lookback = 10
candidate_selection = "nearest"
zone_basis = "full_range"
allow_doji = false
structure_requirement = "bos_or_choch"
require_fvg = false

[premium_discount]
equilibrium_half_width_fraction = "0"

[ote]
lower_retracement = "0.62"
upper_retracement = "0.79"
boundary_policy = "inclusive"
price_basis = "close"

[mtf]
enabled = true
primary_timeframe = "15m"
higher_timeframes = ["1h", "4h"]
availability_policy = "completed_candle"

[halal_filter]
mode = "allow_list"
allowed_assets = [
  "BTCUSDT",
  "ETHUSDT",
  "BNBUSDT",
  "SOLUSDT",
]

[setup_quality]
publish_threshold = 10

[signal_eligibility]
enabled = true
conflict_policy = "neutral"

[signal_engine]
enabled = true
publish_threshold = 10
spot_only = true
duplicate_policy = "one_per_setup"

[outcome_tracking]
enabled = true
horizon_bars = 10

[setup_attribution]
enabled = true

[performance]
enabled = true
minimum_finalized_for_ranking = 10
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "backtest.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_backtest_config_is_enabled() -> None:
    assert BacktestConfig().enabled is True


@pytest.mark.parametrize("enabled", [False, 1, "true", None])
def test_backtest_config_rejects_disabled_or_non_boolean(enabled: object) -> None:
    with pytest.raises(AnalysisConfigurationError):
        BacktestConfig(enabled=enabled)  # type: ignore[arg-type]


def test_load_backtest_config_reads_exact_table(tmp_path: Path) -> None:
    config = load_backtest_config(write(tmp_path, FULL_TOML))
    assert config == BacktestConfig()


def test_load_backtest_config_rejects_missing_table(tmp_path: Path) -> None:
    path = write(tmp_path, "[other]\nenabled = true\n")
    with pytest.raises(AnalysisConfigurationError, match=r"\[backtest\] must contain exactly"):
        load_backtest_config(path)


def test_load_backtest_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = write(tmp_path, "[backtest]\nenabled = true\noptimize = true\n")
    with pytest.raises(AnalysisConfigurationError, match=r"must contain exactly"):
        load_backtest_config(path)


def test_load_backtest_config_rejects_disabled(tmp_path: Path) -> None:
    path = write(tmp_path, "[backtest]\nenabled = false\n")
    with pytest.raises(AnalysisConfigurationError, match=r"enabled must be true"):
        load_backtest_config(path)


def test_load_backtest_config_rejects_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[backtest\nenabled =", encoding="utf-8")
    with pytest.raises(AnalysisConfigurationError, match=r"cannot load backtest configuration"):
        load_backtest_config(path)


def test_load_backtest_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError, match=r"cannot load backtest configuration"):
        load_backtest_config(tmp_path / "absent.toml")


def test_load_backtest_configuration_reads_every_table(tmp_path: Path) -> None:
    loaded = load_backtest_configuration(write(tmp_path, FULL_TOML))
    expected = configuration()
    assert loaded == expected


def test_load_backtest_configuration_allows_missing_analysis_table(tmp_path: Path) -> None:
    text = FULL_TOML.replace("[analysis]\nfractal_length = 3\n\n", "")
    loaded = load_backtest_configuration(write(tmp_path, text))
    assert loaded.analysis is None
    assert loaded.setup_quality == SetupQualityConfig(10)


def test_load_backtest_configuration_names_missing_tables(tmp_path: Path) -> None:
    text = FULL_TOML.replace("[setup_attribution]\nenabled = true\n\n", "")
    text = text.replace("[performance]\nenabled = true\nminimum_finalized_for_ranking = 10\n", "")
    with pytest.raises(
        AnalysisConfigurationError, match=r"missing tables: performance, setup_attribution"
    ):
        load_backtest_configuration(write(tmp_path, text))


def test_load_backtest_configuration_rejects_threshold_mismatch(tmp_path: Path) -> None:
    text = FULL_TOML.replace(
        "[signal_engine]\nenabled = true\npublish_threshold = 10",
        "[signal_engine]\nenabled = true\npublish_threshold = 20",
    )
    with pytest.raises(AnalysisConfigurationError, match=r"threshold"):
        load_backtest_configuration(write(tmp_path, text))


def test_load_backtest_configuration_propagates_table_errors(tmp_path: Path) -> None:
    text = FULL_TOML.replace("horizon_bars = 10", "horizon_bars = 0")
    with pytest.raises(AnalysisConfigurationError, match=r"horizon_bars"):
        load_backtest_configuration(write(tmp_path, text))


def test_configuration_defaults_construct() -> None:
    config = BacktestConfiguration()
    assert config.backtest == BacktestConfig()
    assert config.liquidity == LiquidityConfig("USDT")
    assert config.analysis is None


def test_configuration_rejects_threshold_mismatch() -> None:
    with pytest.raises(AnalysisConfigurationError, match=r"publish_threshold must equal"):
        BacktestConfiguration(
            setup_quality=SetupQualityConfig(10),
            signal_engine=SignalEngineConfig(publish_threshold=11),
        )


def test_configuration_rejects_wrong_field_types() -> None:
    with pytest.raises(AnalysisConfigurationError, match=r"liquidity must be LiquidityConfig"):
        BacktestConfiguration(liquidity="USDT")  # type: ignore[arg-type]
    with pytest.raises(AnalysisConfigurationError, match=r"analysis must be AnalysisConfig"):
        BacktestConfiguration(analysis=3)  # type: ignore[arg-type]
    with pytest.raises(AnalysisConfigurationError, match=r"backtest requires BacktestConfig"):
        BacktestConfiguration(backtest=None)  # type: ignore[arg-type]


def test_configuration_accepts_matching_thresholds() -> None:
    config = BacktestConfiguration(
        analysis=AnalysisConfig(3),
        liquidity=LiquidityConfig("USDT"),
        displacement=DisplacementConfig(atr_period=3),
        setup_quality=SetupQualityConfig(75),
    )
    assert config.signal_engine.publish_threshold == 75
