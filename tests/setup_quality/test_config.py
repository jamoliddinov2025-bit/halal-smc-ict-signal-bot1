from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.setup_quality import (
    DEFAULT_PUBLISH_THRESHOLD,
    WEIGHTS,
    ScoreComponent,
    SetupQualityAnalyzer,
    SetupQualityConfig,
    load_setup_quality_config,
)

VALID = """[setup_quality]
publish_threshold = 75
"""


def test_documented_default_threshold_is_seventy_five():
    config = SetupQualityConfig()
    assert config.publish_threshold == 75
    assert config.publish_threshold == DEFAULT_PUBLISH_THRESHOLD


def test_weights_are_frozen_methodology_and_sum_to_one_hundred():
    assert sum(WEIGHTS.values()) == 100
    assert tuple(WEIGHTS) == tuple(ScoreComponent)
    assert all(type(value) is int and value > 0 for value in WEIGHTS.values())


@pytest.mark.parametrize("value", [0, 1, 74, 75, 76, 100])
def test_integer_thresholds_inside_the_score_range_are_accepted(value):
    assert SetupQualityConfig(value).publish_threshold == value


@pytest.mark.parametrize(
    "value",
    [-1, 101, 75.0, "75", True, False, None, 75 + 0j],
)
def test_non_integer_or_out_of_range_thresholds_are_rejected(value):
    with pytest.raises(AnalysisConfigurationError):
        SetupQualityConfig(value)


def test_configuration_is_frozen():
    for field in fields(SetupQualityConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(SetupQualityConfig(), field.name, 80)


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_setup_quality_config(path) == SetupQualityConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[setup_quality]\n",
        VALID.replace("75", "75.0"),
        VALID.replace("75", '"75"'),
        VALID.replace("75", "true"),
        VALID.replace("75", "-1"),
        VALID.replace("75", "101"),
        VALID + "weights = {halal = 10}\n",
        VALID + "score = 80\n",
        VALID + "signal = true\n",
        VALID + "probability = 0.8\n",
        VALID + "rank = 1\n",
        VALID.replace("publish_threshold = 75\n", ""),
        "[setup_quality]\nthreshold = 75\n",
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_setup_quality_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_setup_quality_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [False, {}, "config", 75])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        SetupQualityAnalyzer(config)


@pytest.mark.parametrize(
    "name",
    ["example.toml", "binance-public.example.toml", "setup-quality.example.toml"],
)
def test_shipped_configuration_uses_strict_defaults(name):
    assert (
        load_setup_quality_config(Path(__file__).resolve().parents[2] / "config" / name)
        == SetupQualityConfig()
    )


def test_replace_cannot_inject_a_weight_field():
    with pytest.raises(TypeError):
        replace(SetupQualityConfig(), weight=10)
