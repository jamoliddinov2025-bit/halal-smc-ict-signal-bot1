from dataclasses import FrozenInstanceError, fields, replace

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.order_blocks import (
    CandidateSelection,
    OrderBlockAnalyzer,
    OrderBlockConfig,
    StructureRequirement,
    ZoneBasis,
    load_order_block_config,
)

VALID = """[order_blocks]
max_candidate_lookback = 10
candidate_selection = "nearest"
zone_basis = "full_range"
allow_doji = false
structure_requirement = "bos_or_choch"
require_fvg = false
"""


def test_conservative_defaults():
    cfg = OrderBlockConfig()
    assert (
        cfg.max_candidate_lookback == 10 and cfg.candidate_selection == CandidateSelection.NEAREST
    )
    assert cfg.zone_basis == ZoneBasis.FULL_RANGE and not cfg.allow_doji and not cfg.require_fvg
    assert cfg.structure_requirement == StructureRequirement.BOS_OR_CHOCH
    assert OrderBlockAnalyzer().config == cfg


@pytest.mark.parametrize("value", [0, -1, 1001, True, 10.0, "10", None])
def test_invalid_lookback(value):
    with pytest.raises(AnalysisConfigurationError):
        OrderBlockConfig(max_candidate_lookback=value)


@pytest.mark.parametrize("name", ["candidate_selection", "zone_basis", "structure_requirement"])
@pytest.mark.parametrize("value", [None, True, 1, "bad", {}])
def test_typed_rule_enums_required_in_direct_api(name, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(OrderBlockConfig(), **{name: value})


@pytest.mark.parametrize("name", ["allow_doji", "require_fvg"])
@pytest.mark.parametrize("value", [0, 1, None, "false", []])
def test_boolean_flags_do_not_coerce(name, value):
    with pytest.raises(AnalysisConfigurationError):
        replace(OrderBlockConfig(), **{name: value})


def test_defaults_and_all_alternatives_are_immutable():
    for mode in StructureRequirement:
        assert OrderBlockConfig(structure_requirement=mode).structure_requirement == mode
    assert OrderBlockConfig(
        candidate_selection=CandidateSelection.EARLIEST,
        zone_basis=ZoneBasis.BODY,
        allow_doji=True,
        require_fvg=True,
        max_candidate_lookback=1000,
    )
    for field in fields(OrderBlockConfig):
        with pytest.raises(FrozenInstanceError):
            setattr(OrderBlockConfig(), field.name, getattr(OrderBlockConfig(), field.name))


def test_explicit_loader_with_other_metadata(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[other]\nmetadata=true\n" + VALID)
    assert load_order_block_config(path) == OrderBlockConfig()


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "[order_blocks]\n",
        "bad TOML",
        VALID.replace('"nearest"', '"all"'),
        VALID.replace('"full_range"', '"unknown"'),
        VALID.replace('"bos_or_choch"', '"later_break"'),
        VALID.replace("allow_doji = false\n", ""),
        VALID.replace("false", '"false"'),
        VALID + "score=true\n",
        VALID + "lifecycle=true\n",
    ],
)
def test_missing_unknown_and_invalid_settings_fail(tmp_path, contents):
    path = tmp_path / "bad.toml"
    path.write_text(contents)
    with pytest.raises(AnalysisConfigurationError):
        load_order_block_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_order_block_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("value", [False, {}, "config"])
def test_wrong_analyzer_configuration(value):
    with pytest.raises(AnalysisConfigurationError):
        OrderBlockAnalyzer(value)


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "order-block.example.toml"]
)
def test_shipped_order_block_tables_use_exact_defaults(name):
    from pathlib import Path

    assert (
        load_order_block_config(Path(__file__).resolve().parents[2] / "config" / name)
        == OrderBlockConfig()
    )
