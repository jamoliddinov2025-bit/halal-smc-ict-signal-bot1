from dataclasses import FrozenInstanceError, fields, replace

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.mtf import AvailabilityPolicy, MTFAnalyzer, MTFConfig, load_mtf_config

VALID = """[mtf]
enabled = true
primary_timeframe = "15m"
higher_timeframes = ["1h", "4h"]
availability_policy = "completed_candle"
"""


def test_documented_defaults_are_completed_candle_15m_with_1h_and_4h():
    config = MTFConfig()
    assert config.enabled is True
    assert config.primary_timeframe == "15m"
    assert config.higher_timeframes == ("1h", "4h")
    assert config.availability_policy is AvailabilityPolicy.COMPLETED_CANDLE


@pytest.mark.parametrize(
    "primary,higher",
    [
        ("15m", ("1h",)),
        ("15m", ("1h", "4h", "1d")),
        ("5m", ("15m", "1h")),
        ("1h", ("4h", "1d")),
        ("1d", ("1w",)),
        ("1s", ("1m", "1h")),
    ],
)
def test_valid_integer_multiple_pairs_are_accepted(primary, higher):
    config = MTFConfig(primary_timeframe=primary, higher_timeframes=higher)
    assert config.higher_timeframes == higher


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", False),
        ("enabled", 1),
        ("enabled", "true"),
        ("primary_timeframe", "1M"),
        ("primary_timeframe", "15M"),
        ("primary_timeframe", "1h "),
        ("primary_timeframe", 15),
        ("higher_timeframes", ()),
        ("higher_timeframes", ["1h", "4h"]),
        ("higher_timeframes", ("1h", "1h")),
        ("higher_timeframes", ("15m",)),
        ("higher_timeframes", ("1h", "15m")),
        ("higher_timeframes", ("5m",)),
        ("higher_timeframes", ("1M",)),
        ("higher_timeframes", (1,)),
        ("availability_policy", "close_time"),
        ("availability_policy", "completed_candle"),
        ("availability_policy", None),
    ],
)
def test_untyped_disabled_or_invalid_relationships_are_rejected(field, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(MTFConfig(), **{field: value})


def test_primary_1h_cannot_keep_default_1h_higher_timeframe():
    with pytest.raises(AnalysisConfigurationError):
        MTFConfig(primary_timeframe="1h")


def test_3d_is_not_an_integer_multiple_of_1w_or_the_reverse():
    with pytest.raises(AnalysisConfigurationError):
        MTFConfig(primary_timeframe="3d", higher_timeframes=("1w",))
    with pytest.raises(AnalysisConfigurationError):
        MTFConfig(primary_timeframe="1w", higher_timeframes=("3d",))


def test_configuration_is_frozen():
    for field in fields(MTFConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(MTFConfig(), field.name, None)


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_mtf_config(path) == MTFConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[mtf]\n",
        VALID.replace("true", "false"),
        VALID.replace("15m", "1M"),
        VALID.replace('["1h", "4h"]', '["1h", "1h"]'),
        VALID.replace('["1h", "4h"]', '["15m", "1h"]'),
        VALID.replace('["1h", "4h"]', "[]"),
        VALID.replace('["1h", "4h"]', '"1h"'),
        VALID.replace("completed_candle", "open_time"),
        VALID + "score=true\n",
        VALID + "weight=1\n",
        VALID.replace("enabled = true\n", ""),
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_mtf_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_mtf_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [False, {}, "config"])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        MTFAnalyzer(config, higher={"1h": (), "4h": ()})


@pytest.mark.parametrize(
    "name",
    ["example.toml", "binance-public.example.toml", "mtf.example.toml"],
)
def test_shipped_configuration_uses_strict_defaults(name):
    from pathlib import Path

    assert load_mtf_config(Path(__file__).resolve().parents[2] / "config" / name) == MTFConfig()
