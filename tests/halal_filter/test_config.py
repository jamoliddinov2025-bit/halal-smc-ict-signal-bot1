from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.halal_filter import (
    FilterMode,
    HalalFilterAnalyzer,
    HalalFilterConfig,
    load_halal_filter_config,
)

VALID = """[halal_filter]
mode = "allow_list"
allowed_assets = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
"""

DENY = """[halal_filter]
mode = "deny_list"
denied_assets = ["XYZUSDT"]
"""


def test_documented_defaults_are_allow_list_of_major_spot_pairs():
    config = HalalFilterConfig()
    assert config.mode is FilterMode.ALLOW_LIST
    assert config.allowed_assets == ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
    assert config.denied_assets == ()


def test_deny_list_requires_empty_allow_list_and_nonempty_denied_assets():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )
    assert config.mode is FilterMode.DENY_LIST
    assert config.denied_assets == ("XYZUSDT",)
    assert config.allowed_assets == ()


def test_asset_symbols_are_normalized_to_uppercase():
    config = HalalFilterConfig(allowed_assets=("btcusdt", "ethUSDT"))
    assert config.allowed_assets == ("BTCUSDT", "ETHUSDT")
    denied = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("xyzusdt",)
    )
    assert denied.denied_assets == ("XYZUSDT",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "allow_list"),
        ("mode", "deny_list"),
        ("mode", None),
        ("mode", True),
        ("allowed_assets", ["BTCUSDT"]),
        ("allowed_assets", ()),
        ("allowed_assets", ("BTCUSDT", "BTCUSDT")),
        ("allowed_assets", ("BTC-USDT",)),
        ("allowed_assets", ("B",)),
        ("allowed_assets", (123,)),
        ("denied_assets", ("XYZUSDT",)),
        ("denied_assets", ["XYZUSDT"]),
    ],
)
def test_untyped_empty_or_conflicting_settings_are_rejected(field, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(HalalFilterConfig(), **{field: value})


def test_deny_list_defaults_are_rejected_without_explicit_lists():
    with pytest.raises(AnalysisConfigurationError):
        HalalFilterConfig(mode=FilterMode.DENY_LIST)


def test_configuration_is_frozen():
    for field in fields(HalalFilterConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(HalalFilterConfig(), field.name, None)


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_halal_filter_config(path) == HalalFilterConfig()


def test_deny_list_toml_uses_denied_assets_key(tmp_path):
    path = tmp_path / "deny.toml"
    path.write_text(DENY)
    assert load_halal_filter_config(path) == HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[halal_filter]\n",
        VALID.replace("allow_list", "whitelist"),
        VALID.replace("allow_list", "deny_list"),
        VALID.replace('["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]', "[]"),
        VALID.replace('["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]', '"BTCUSDT"'),
        VALID.replace('["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]', '["BTCUSDT", "BTCUSDT"]'),
        VALID.replace("BTCUSDT", "BTC-USDT"),
        VALID + "denied_assets = []\n",
        VALID + "score = true\n",
        VALID + "signal = true\n",
        VALID.replace('mode = "allow_list"\n', ""),
        DENY.replace("XYZUSDT", "BTC-USDT"),
        DENY + 'allowed_assets = ["BTCUSDT"]\n',
        '[halal_filter]\nmode = "deny_list"\ndenied_assets = []\n',
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_halal_filter_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_halal_filter_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [False, {}, "config"])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        HalalFilterAnalyzer(config)


@pytest.mark.parametrize(
    "name",
    ["example.toml", "binance-public.example.toml", "halal-filter.example.toml"],
)
def test_shipped_configuration_uses_strict_defaults(name):
    assert (
        load_halal_filter_config(Path(__file__).resolve().parents[2] / "config" / name)
        == HalalFilterConfig()
    )
