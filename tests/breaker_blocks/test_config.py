from dataclasses import FrozenInstanceError, fields, replace

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.breaker_blocks import (
    BreakerBlockAnalyzer,
    BreakerBlockConfig,
    BreakerZoneBasis,
    InvalidationBasis,
    load_breaker_block_config,
)

VALID = """[breaker_blocks]
require_displacement = true
require_mss = true
invalidation_basis = "close_through_far_boundary"
zone_basis = "original_order_block"
"""


def test_strict_defaults_match_declared_operational_rules():
    config = BreakerBlockConfig()
    assert config.require_displacement is config.require_mss is True
    assert config.invalidation_basis == InvalidationBasis.CLOSE_THROUGH_FAR_BOUNDARY
    assert config.zone_basis == BreakerZoneBasis.ORIGINAL_ORDER_BLOCK
    assert BreakerBlockAnalyzer().config == config


@pytest.mark.parametrize("name", ["require_displacement", "require_mss"])
@pytest.mark.parametrize("value", [False, None, 0, 1, "true", []])
def test_mandatory_confirmation_cannot_be_disabled_or_coerced(name, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(BreakerBlockConfig(), **{name: value})


@pytest.mark.parametrize("name", ["invalidation_basis", "zone_basis"])
@pytest.mark.parametrize("value", [None, "wick", "body", True, {}, "original_order_block"])
def test_direct_api_requires_typed_supported_rule_enums(name, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(BreakerBlockConfig(), **{name: value})


def test_configuration_is_immutable():
    for field in fields(BreakerBlockConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(BreakerBlockConfig(), field.name, None)


def test_explicit_toml_loader_consumes_only_its_table(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[metadata]\nsynthetic=true\n" + VALID)
    assert load_breaker_block_config(path) == BreakerBlockConfig()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "bad TOML",
        "[breaker_blocks]\n",
        VALID.replace("require_mss = true\n", ""),
        VALID.replace("true", "false"),
        VALID.replace("true", '"true"'),
        VALID.replace("close_through_far_boundary", "wick"),
        VALID.replace("original_order_block", "body"),
        VALID + "score=true\n",
        VALID + "require_fvg=true\n",
        VALID + "retest=true\n",
    ],
)
def test_incomplete_unknown_and_unsafe_options_are_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(AnalysisConfigurationError):
        load_breaker_block_config(path)


def test_missing_file_is_explicit_error(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_breaker_block_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [False, {}, "config"])
def test_wrong_analyzer_config_type(config):
    with pytest.raises(AnalysisConfigurationError):
        BreakerBlockAnalyzer(config)


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "breaker-block.example.toml"]
)
def test_shipped_configuration_uses_strict_defaults(name):
    from pathlib import Path

    assert (
        load_breaker_block_config(Path(__file__).resolve().parents[2] / "config" / name)
        == BreakerBlockConfig()
    )
