from dataclasses import FrozenInstanceError, fields, replace

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.mss import MSSAnalyzer, MSSConfig, load_mss_config

VALID = "[mss]\nenable_displacement_requirement=true\nenable_structure_requirement=true\n"


def test_both_strict_requirements_are_enabled_and_cannot_be_weakened():
    assert MSSConfig().enable_displacement_requirement is True
    assert MSSConfig().enable_structure_requirement is True
    assert MSSAnalyzer().config == MSSConfig()


@pytest.mark.parametrize(
    "name", ["enable_displacement_requirement", "enable_structure_requirement"]
)
@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "false", []])
def test_disabled_or_untyped_requirements_are_rejected(name, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(MSSConfig(), **{name: value})


def test_requirement_contract_is_immutable():
    for field in fields(MSSConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(MSSConfig(), field.name, False)


def test_valid_explicit_configuration(tmp_path):
    p = tmp_path / "mss.toml"
    p.write_text("[metadata]\ntest=true\n" + VALID)
    assert load_mss_config(p) == MSSConfig()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "invalid TOML",
        "[mss]\n",
        VALID.replace("enable_structure_requirement=true\n", ""),
        VALID.replace("true", "false"),
        VALID + "score=true\n",
        VALID + "signal_threshold=1\n",
        VALID.replace("true", '"true"'),
    ],
)
def test_missing_unknown_or_unsafe_options_fail(tmp_path, text):
    p = tmp_path / "bad.toml"
    p.write_text(text)
    with pytest.raises(AnalysisConfigurationError):
        load_mss_config(p)


def test_missing_file(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_mss_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [{}, False, "config"])
def test_analyzer_rejects_wrong_configuration_type(config):
    with pytest.raises(AnalysisConfigurationError):
        MSSAnalyzer(config)


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "mss.example.toml"]
)
def test_shipped_mss_tables_use_strict_defaults(name):
    from pathlib import Path

    assert load_mss_config(Path(__file__).resolve().parents[2] / "config" / name) == MSSConfig()
