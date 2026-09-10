from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.premium_discount import PDAnalyzer, PDConfig, load_pd_config

VALID = '[premium_discount]\nequilibrium_half_width_fraction = "0"\n'


def test_exact_midpoint_is_default():
    assert PDConfig().equilibrium_half_width_fraction == 0
    assert PDAnalyzer().config == PDConfig()


@pytest.mark.parametrize(
    "value", [Decimal("0"), Decimal("0.05"), Decimal("0.5"), Decimal("0.000000001"), Decimal("-0")]
)
def test_valid_equilibrium_band_fraction(value):
    assert PDConfig(value).equilibrium_half_width_fraction == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        True,
        "0",
        0.1,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-0.001"),
        Decimal("0.5000001"),
    ],
)
def test_invalid_fraction_type_or_range(value):
    with pytest.raises(AnalysisConfigurationError):
        PDConfig(value)


def test_config_is_frozen():
    with pytest.raises(FrozenInstanceError):
        PDConfig().equilibrium_half_width_fraction = Decimal("0.1")


def test_exact_explicit_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text('[metadata]\ndescription="test"\n' + VALID)
    assert load_pd_config(path) == PDConfig()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "bad TOML",
        "[premium_discount]\n",
        VALID.replace('"0"', "0.0"),
        VALID.replace('"0"', '"bad"'),
        VALID + "score=true\n",
        VALID + "htf=true\n",
        VALID + "range_lookback=10\n",
    ],
)
def test_unknown_missing_or_invalid_settings(tmp_path, text):
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(AnalysisConfigurationError):
        load_pd_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_pd_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [{}, "config", False])
def test_wrong_analyzer_config(config):
    with pytest.raises(AnalysisConfigurationError):
        PDAnalyzer(config)


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "premium-discount.example.toml"]
)
def test_shipped_pd_tables_use_exact_defaults(name):
    from pathlib import Path

    assert load_pd_config(Path(__file__).resolve().parents[2] / "config" / name) == PDConfig()
