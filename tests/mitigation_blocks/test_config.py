from dataclasses import FrozenInstanceError, fields, replace

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.mitigation_blocks import (
    InteractionBasis,
    MitigationBlockAnalyzer,
    MitigationBlockConfig,
    load_mitigation_block_config,
)

VALID = """[mitigation_blocks]
interaction_basis = "range_intersection"
first_interaction_only = true
ignore_after_breaker = true
"""


def test_strict_defaults_match_declared_operational_rules():
    config = MitigationBlockConfig()
    assert config.interaction_basis == InteractionBasis.RANGE_INTERSECTION
    assert config.first_interaction_only is config.ignore_after_breaker is True
    assert MitigationBlockAnalyzer().config == config


@pytest.mark.parametrize("name", ["first_interaction_only", "ignore_after_breaker"])
@pytest.mark.parametrize("value", [False, None, 0, 1, "true", []])
def test_mandatory_interaction_policy_cannot_be_disabled_or_coerced(name, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(MitigationBlockConfig(), **{name: value})


@pytest.mark.parametrize("value", [None, "wick", "body", True, {}, "close_through"])
def test_direct_api_requires_typed_supported_interaction_basis(value):
    with pytest.raises(AnalysisConfigurationError):
        replace(MitigationBlockConfig(), interaction_basis=value)


def test_configuration_is_immutable():
    for field in fields(MitigationBlockConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(MitigationBlockConfig(), field.name, None)


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_mitigation_block_config(path) == MitigationBlockConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[mitigation_blocks]\n",
        VALID.replace("first_interaction_only = true\n", ""),
        VALID.replace("true", "false"),
        VALID.replace("true", '"true"'),
        VALID.replace("range_intersection", "wick"),
        VALID + "score=true\n",
        VALID + "retest=true\n",
        VALID + "entry=true\n",
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_mitigation_block_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_mitigation_block_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [False, {}, "config"])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        MitigationBlockAnalyzer(config)


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "mitigation-block.example.toml"]
)
def test_shipped_configuration_uses_strict_defaults(name):
    from pathlib import Path

    assert (
        load_mitigation_block_config(Path(__file__).resolve().parents[2] / "config" / name)
        == MitigationBlockConfig()
    )
