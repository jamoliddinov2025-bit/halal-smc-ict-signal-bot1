from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.signal_eligibility import (
    ConflictPolicy,
    SignalEligibilityAnalyzer,
    SignalEligibilityConfig,
    load_signal_eligibility_config,
)

VALID = """[signal_eligibility]
enabled = true
conflict_policy = "neutral"
"""


def test_documented_defaults_require_enabled_neutral_conflict_policy():
    config = SignalEligibilityConfig()
    assert config.enabled is True
    assert config.conflict_policy is ConflictPolicy.NEUTRAL


def test_configuration_is_frozen():
    for field in fields(SignalEligibilityConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(SignalEligibilityConfig(), field.name, None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", False),
        ("enabled", 1),
        ("enabled", "true"),
        ("enabled", None),
        ("conflict_policy", "force_long"),
        ("conflict_policy", "mixed"),
        ("conflict_policy", True),
        ("conflict_policy", None),
    ],
)
def test_disabled_or_unsafe_settings_are_rejected(field, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(SignalEligibilityConfig(), **{field: value})


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_signal_eligibility_config(path) == SignalEligibilityConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[signal_eligibility]\n",
        VALID.replace("true", "false"),
        VALID.replace("true", "1"),
        VALID.replace('"neutral"', '"force_long"'),
        VALID.replace('"neutral"', "true"),
        VALID + "signal = true\n",
        VALID + "entry = true\n",
        VALID + "buy = true\n",
        VALID + "sell = true\n",
        VALID + "telegram = true\n",
        VALID.replace("enabled = true\n", ""),
        VALID.replace('conflict_policy = "neutral"\n', ""),
        "[signal_eligibility]\nenabled = true\n",
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_signal_eligibility_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_signal_eligibility_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [False, {}, "config", 75])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        SignalEligibilityAnalyzer(config)


@pytest.mark.parametrize(
    "name",
    [
        "example.toml",
        "binance-public.example.toml",
        "setup-quality.example.toml",
        "signal-eligibility.example.toml",
    ],
)
def test_shipped_configuration_uses_strict_defaults(name):
    assert (
        load_signal_eligibility_config(Path(__file__).resolve().parents[2] / "config" / name)
        == SignalEligibilityConfig()
    )
