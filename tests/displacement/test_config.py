from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.displacement import (
    DisplacementAnalyzer,
    DisplacementConfig,
    load_displacement_config,
)

VALID = """[displacement]
atr_period = 14
min_body_atr = "1.0"
min_range_atr = "1.5"
bullish_close_min = "0.70"
bearish_close_max = "0.30"
atr_floor = "0"
sweep_lookback_bars = 20
"""


def test_defaults_are_explicit_conservative_and_not_scattered():
    cfg = DisplacementConfig()
    assert cfg.atr_period == 14 and cfg.sweep_lookback_bars == 20
    assert cfg.min_body_atr == Decimal("1.0") and cfg.min_range_atr == Decimal("1.5")
    assert cfg.bullish_close_min == Decimal("0.70") and cfg.bearish_close_max == Decimal("0.30")
    assert cfg.atr_floor == 0


@pytest.mark.parametrize("period", [0, -1, 1001, True, "14", 3.0, None])
def test_invalid_atr_period(period):
    with pytest.raises(AnalysisConfigurationError):
        DisplacementConfig(atr_period=period)


@pytest.mark.parametrize("lookback", [-1, 10001, True, "20", 1.0, None])
def test_invalid_context_lookback(lookback):
    with pytest.raises(AnalysisConfigurationError):
        DisplacementConfig(sweep_lookback_bars=lookback)


@pytest.mark.parametrize(
    "name", ["min_body_atr", "min_range_atr", "bullish_close_min", "bearish_close_max", "atr_floor"]
)
@pytest.mark.parametrize("bad", [None, 1, "1", 0.1, True, Decimal("NaN"), Decimal("Infinity")])
def test_decimal_fields_reject_untyped_and_nonfinite_values(name, bad):
    with pytest.raises(AnalysisConfigurationError):
        replace(DisplacementConfig(), **{name: bad})


@pytest.mark.parametrize(
    "name,value",
    [
        ("min_body_atr", "0"),
        ("min_body_atr", "-1"),
        ("min_body_atr", "1001"),
        ("min_range_atr", "0"),
        ("min_range_atr", "-1"),
        ("min_range_atr", "1001"),
        ("bullish_close_min", "1.01"),
        ("bullish_close_min", "-0.01"),
        ("bearish_close_max", "1.01"),
        ("bearish_close_max", "-0.01"),
        ("atr_floor", "-1"),
    ],
)
def test_invalid_numeric_ranges(name, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(DisplacementConfig(), **{name: Decimal(value)})


def test_reversed_close_thresholds_rejected():
    with pytest.raises(AnalysisConfigurationError):
        DisplacementConfig(bullish_close_min=Decimal("0.2"), bearish_close_max=Decimal("0.8"))


def test_valid_configuration_extremes():
    assert DisplacementConfig(atr_period=1, sweep_lookback_bars=0).atr_period == 1
    assert DisplacementConfig(atr_period=1000, sweep_lookback_bars=10000).atr_period == 1000
    assert (
        DisplacementConfig(
            bullish_close_min=Decimal(1), bearish_close_max=Decimal(0)
        ).bearish_close_max
        == 0
    )


def test_configuration_is_immutable():
    with pytest.raises(FrozenInstanceError):
        DisplacementConfig().atr_period = 1


def test_explicit_toml_loads_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[unrelated]\nmetadata=true\n" + VALID)
    assert load_displacement_config(path) == DisplacementConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "[displacement]\n",
        "invalid TOML",
        VALID.replace('"1.0"', "1.0"),
        VALID.replace('"1.0"', '"bad"'),
        VALID.replace("atr_period = 14", "atr_period = true"),
        VALID + "scoring = true\n",
        VALID.replace('atr_floor = "0"\n', ""),
    ],
)
def test_missing_unknown_or_malformed_table_is_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_displacement_config(path)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_displacement_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("unit", [None, "", " ", " USDT", 1])
def test_explicit_price_unit_is_required_and_validated(unit):
    with pytest.raises(AnalysisConfigurationError):
        DisplacementAnalyzer(price_unit=unit)


@pytest.mark.parametrize("cfg", [{}, "config", False])
def test_analyzer_rejects_untyped_configuration(cfg):
    with pytest.raises(AnalysisConfigurationError):
        DisplacementAnalyzer(cfg, price_unit="USDT")


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "displacement.example.toml"]
)
def test_included_displacement_settings_match_defaults(name):
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "config" / name
    assert load_displacement_config(path) == DisplacementConfig()
