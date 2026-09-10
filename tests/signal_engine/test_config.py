from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.setup_quality import DEFAULT_PUBLISH_THRESHOLD
from smcsignal.analysis.signal_engine import (
    DuplicatePolicy,
    SignalEngineAnalyzer,
    SignalEngineConfig,
    load_signal_engine_config,
)

VALID = """[signal_engine]
enabled = true
publish_threshold = 75
spot_only = true
duplicate_policy = "one_per_setup"
"""


def test_documented_defaults_require_enabled_spot_only_and_default_threshold():
    config = SignalEngineConfig()
    assert config.enabled is True
    assert config.spot_only is True
    assert config.publish_threshold == 75
    assert config.publish_threshold == DEFAULT_PUBLISH_THRESHOLD
    assert config.duplicate_policy is DuplicatePolicy.ONE_PER_SETUP


def test_configuration_is_frozen():
    for field in fields(SignalEngineConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(SignalEngineConfig(), field.name, None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", False),
        ("enabled", 1),
        ("enabled", "true"),
        ("enabled", None),
        ("spot_only", False),
        ("spot_only", 1),
        ("spot_only", "true"),
        ("duplicate_policy", "allow_repeats"),
        ("duplicate_policy", "none"),
        ("duplicate_policy", True),
        ("duplicate_policy", None),
        ("publish_threshold", -1),
        ("publish_threshold", 101),
        ("publish_threshold", 75.0),
        ("publish_threshold", "75"),
        ("publish_threshold", True),
    ],
)
def test_disabled_or_unsafe_settings_are_rejected(field, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(SignalEngineConfig(), **{field: value})


@pytest.mark.parametrize("value", [0, 1, 74, 75, 76, 100])
def test_integer_thresholds_inside_the_score_range_are_accepted(value):
    assert SignalEngineConfig(publish_threshold=value).publish_threshold == value


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_signal_engine_config(path) == SignalEngineConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[signal_engine]\n",
        VALID.replace("true", "false", 1),
        VALID.replace("spot_only = true", "spot_only = false"),
        VALID.replace("75", "75.0"),
        VALID.replace("75", '"75"'),
        VALID.replace('"one_per_setup"', '"allow_repeats"'),
        VALID.replace('"one_per_setup"', "true"),
        VALID + "entry = true\n",
        VALID + "stop_loss = true\n",
        VALID + "telegram = true\n",
        VALID + "rank = 1\n",
        VALID.replace("enabled = true\n", ""),
        VALID.replace("spot_only = true\n", ""),
        VALID.replace('duplicate_policy = "one_per_setup"\n', ""),
        VALID.replace("publish_threshold = 75\n", ""),
        "[signal_engine]\nenabled = true\nspot_only = true\n",
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_signal_engine_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_signal_engine_config(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    "name",
    [
        "example.toml",
        "binance-public.example.toml",
        "setup-quality.example.toml",
        "signal-eligibility.example.toml",
        "signal-engine.example.toml",
    ],
)
def test_shipped_configuration_uses_strict_defaults(name):
    assert (
        load_signal_engine_config(Path(__file__).resolve().parents[2] / "config" / name)
        == SignalEngineConfig()
    )


def test_replace_cannot_inject_an_execution_field():
    with pytest.raises(TypeError):
        replace(SignalEngineConfig(), entry=True)


@pytest.mark.parametrize("config", [False, {}, "config", 75])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        SignalEngineAnalyzer(config)
