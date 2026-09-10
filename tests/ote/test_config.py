from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.ote import (
    BoundaryPolicy,
    OTEAnalyzer,
    OTEConfig,
    PriceBasis,
    load_ote_config,
)

VALID = """[ote]
lower_retracement = "0.62"
upper_retracement = "0.79"
boundary_policy = "inclusive"
price_basis = "close"
"""


def test_documented_defaults_are_explicit_decimal_ratios():
    config = OTEConfig()
    assert config.lower_retracement == Decimal("0.62")
    assert config.upper_retracement == Decimal("0.79")
    assert config.boundary_policy == BoundaryPolicy.INCLUSIVE
    assert config.price_basis == PriceBasis.CLOSE
    assert OTEAnalyzer().config == config


@pytest.mark.parametrize(
    "lower,upper",
    [
        (Decimal("0.000000001"), Decimal("0.999999999")),
        (Decimal("0.5"), Decimal("0.5000001")),
        (Decimal("0.618"), Decimal("0.786")),
    ],
)
def test_explicit_finite_ratios_inside_open_unit_interval_are_accepted(lower, upper):
    assert OTEConfig(lower, upper).lower_retracement == lower
    assert OTEConfig(lower, upper).upper_retracement == upper


@pytest.mark.parametrize(
    "lower,upper",
    [
        (Decimal(0), Decimal("0.79")),
        (Decimal("0.62"), Decimal(1)),
        (Decimal("0.79"), Decimal("0.62")),
        (Decimal("0.62"), Decimal("0.62")),
        (Decimal("-0.1"), Decimal("0.79")),
        (Decimal("0.62"), Decimal("1.01")),
    ],
)
def test_zero_one_reversed_or_equal_retracements_are_rejected(lower, upper):
    with pytest.raises(AnalysisConfigurationError):
        OTEConfig(lower, upper)


@pytest.mark.parametrize(
    "field,value",
    [
        ("lower_retracement", None),
        ("lower_retracement", 0.62),
        ("lower_retracement", "0.62"),
        ("lower_retracement", True),
        ("lower_retracement", Decimal("NaN")),
        ("lower_retracement", Decimal("Infinity")),
        ("upper_retracement", 0.79),
        ("boundary_policy", "exclusive"),
        ("boundary_policy", "inclusive"),
        ("boundary_policy", None),
        ("price_basis", "wick"),
        ("price_basis", "close"),
        ("price_basis", None),
    ],
)
def test_untyped_or_unsupported_direct_settings_are_rejected(field, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(OTEConfig(), **{field: value})


def test_configuration_is_frozen():
    for field in fields(OTEConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(OTEConfig(), field.name, None)


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_ote_config(path) == OTEConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[ote]\n",
        VALID.replace('"0.62"', "0.62"),
        VALID.replace('"0.79"', "0.79"),
        VALID.replace('"0.62"', '"bad"'),
        VALID.replace("inclusive", "exclusive"),
        VALID.replace("close", "high"),
        VALID.replace("close", "wick"),
        VALID + "score=true\n",
        VALID + "entry=true\n",
        VALID + "golden_ratio=true\n",
        VALID.replace('lower_retracement = "0.62"\n', ""),
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_ote_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_ote_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [False, {}, "config"])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        OTEAnalyzer(config)


@pytest.mark.parametrize(
    "name",
    [
        "example.toml",
        "binance-public.example.toml",
        "premium-discount.example.toml",
        "ote.example.toml",
    ],
)
def test_shipped_configuration_uses_strict_defaults(name):
    from pathlib import Path

    assert load_ote_config(Path(__file__).resolve().parents[2] / "config" / name) == OTEConfig()
