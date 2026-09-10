from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.fvg import FVGAnalyzer, FVGConfig, load_fvg_config

VALID = '[fvg]\nmin_gap_size = "0"\nrequire_displacement = false\n'


def test_defaults_do_not_assume_a_tick_size_or_require_displacement():
    assert FVGConfig() == FVGConfig(Decimal(0), False)
    assert FVGAnalyzer().config == FVGConfig()


@pytest.mark.parametrize(
    "minimum", [Decimal("0"), Decimal("0.00000001"), Decimal("1e20"), Decimal("-0")]
)
def test_nonnegative_decimal_distances(minimum):
    assert FVGConfig(minimum).min_gap_size == minimum


@pytest.mark.parametrize(
    "minimum", [None, "0", 0, 0.1, False, Decimal("-1"), Decimal("NaN"), Decimal("Infinity")]
)
def test_invalid_minimum_type_and_value(minimum):
    with pytest.raises(AnalysisConfigurationError):
        FVGConfig(minimum)


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true", [], Decimal(0)])
def test_displacement_requirement_is_strict_boolean(flag):
    with pytest.raises(AnalysisConfigurationError):
        FVGConfig(require_displacement=flag)


def test_config_is_frozen():
    with pytest.raises(FrozenInstanceError):
        FVGConfig().require_displacement = True


def test_valid_explicit_table_with_other_layers(tmp_path):
    path = tmp_path / "valid.toml"
    path.write_text("[other]\nmetadata=true\n" + VALID)
    assert load_fvg_config(path) == FVGConfig()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "invalid TOML",
        "[fvg]\n",
        VALID.replace('"0"', "0.0"),
        VALID.replace('"0"', '"invalid"'),
        VALID.replace("false", '"false"'),
        VALID.replace("require_displacement = false\n", ""),
        VALID + "scoring=true\n",
        VALID + "atr_period=14\n",
        VALID + "lifecycle=true\n",
    ],
)
def test_missing_unknown_or_invalid_configuration(tmp_path, text):
    path = tmp_path / "invalid.toml"
    path.write_text(text)
    with pytest.raises(AnalysisConfigurationError):
        load_fvg_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_fvg_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("config", [True, {}, "config"])
def test_analyzer_rejects_wrong_configuration_type(config):
    with pytest.raises(AnalysisConfigurationError):
        FVGAnalyzer(config)


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "fvg.example.toml"]
)
def test_shipped_fvg_tables_match_defaults(name):
    from pathlib import Path

    assert load_fvg_config(Path(__file__).resolve().parents[2] / "config" / name) == FVGConfig()
