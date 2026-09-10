from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.indicators import IndicatorsConfig, load_indicators_config
from smcsignal.analysis.indicators.config import MAX_PERIOD, METHODOLOGY_VERSION

PATH = Path(__file__).resolve().parents[2] / "config" / "indicators.example.toml"


def test_defaults_are_frozen_and_explicit() -> None:
    config = IndicatorsConfig()
    assert config.enabled is True
    assert config.ema_periods == (20, 50)
    assert config.rsi_period == 14
    assert config.volume_average_period == 20
    assert METHODOLOGY_VERSION == "indicators-v1"
    with pytest.raises(FrozenInstanceError):
        config.rsi_period = 5  # type: ignore[misc]


def test_ema_periods_must_be_nonempty_strictly_increasing_ints() -> None:
    assert IndicatorsConfig(ema_periods=(3,)).ema_periods == (3,)
    assert IndicatorsConfig(ema_periods=(5, 10, 100)).ema_periods == (5, 10, 100)
    for invalid in ((), (5, 5), (50, 20), (0, 20), (20, MAX_PERIOD + 1), ("20", 50)):
        with pytest.raises(AnalysisConfigurationError):
            IndicatorsConfig(ema_periods=invalid)  # type: ignore[arg-type]
    with pytest.raises(AnalysisConfigurationError):
        IndicatorsConfig(ema_periods=[20, 50])  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["rsi_period", "volume_average_period"])
def test_periods_have_strict_bounds(field: str) -> None:
    for invalid in (0, -1, MAX_PERIOD + 1, 1.0, True, "14", None):
        with pytest.raises(AnalysisConfigurationError):
            IndicatorsConfig(**{field: invalid})  # type: ignore[arg-type]


def test_enabled_must_be_true_and_boolean() -> None:
    with pytest.raises(AnalysisConfigurationError):
        IndicatorsConfig(enabled=False)
    for invalid in (1, "true", None):
        with pytest.raises(AnalysisConfigurationError):
            IndicatorsConfig(enabled=invalid)  # type: ignore[arg-type]


def write_table(tmp_path: Path, table: str) -> Path:
    path = tmp_path / "indicators.toml"
    path.write_text(table, encoding="utf-8")
    return path


def test_loader_requires_the_exact_table(tmp_path: Path) -> None:
    for table in (
        "",
        "[other]\nenabled = true\n",
        "[indicators]\nenabled = true\n",
        "[indicators]\nema_periods = [20, 50]\nrsi_period = 14\nvolume_average_period = 20\n",
        "[indicators]\nenabled = true\nrsi_period = 14\nvolume_average_period = 20\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nvolume_average_period = 20\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nrsi_period = 14\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nrsi_period = 14\n"
        "volume_average_period = 20\nthreshold = 70\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nrsi_period = 14\n"
        "volume_average_period = 20\ngate = false\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nrsi_period = 14\n"
        "volume_average_period = 20\nsignal_generation = false\n",
    ):
        with pytest.raises(AnalysisConfigurationError):
            load_indicators_config(write_table(tmp_path, table))


@pytest.mark.parametrize(
    "table",
    [
        "[indicators]\nenabled = false\nema_periods = [20, 50]\nrsi_period = 14\n"
        "volume_average_period = 20\n",
        "[indicators]\nenabled = true\nema_periods = []\nrsi_period = 14\n"
        "volume_average_period = 20\n",
        "[indicators]\nenabled = true\nema_periods = [50, 20]\nrsi_period = 14\n"
        "volume_average_period = 20\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nrsi_period = 0\n"
        "volume_average_period = 20\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nrsi_period = 14.5\n"
        "volume_average_period = 20\n",
        "[indicators]\nenabled = true\nema_periods = [20, 50]\nrsi_period = 14\n"
        "volume_average_period = true\n",
    ],
)
def test_loader_rejects_invalid_values(tmp_path: Path, table: str) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_indicators_config(write_table(tmp_path, table))


def test_loader_accepts_the_exact_approved_table(tmp_path: Path) -> None:
    loaded = load_indicators_config(
        write_table(
            tmp_path,
            "[indicators]\nenabled = true\nema_periods = [3, 5]\nrsi_period = 3\n"
            "volume_average_period = 4\n",
        )
    )
    assert loaded == IndicatorsConfig(ema_periods=(3, 5), rsi_period=3, volume_average_period=4)
    assert isinstance(loaded.ema_periods, tuple)


def test_example_configuration_loads_and_matches_defaults() -> None:
    loaded = load_indicators_config(PATH)
    assert loaded == IndicatorsConfig()


def test_loader_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_indicators_config(tmp_path / "missing.toml")
