"""Phase 22 intelligence configuration invariants."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.intelligence import IntelligenceConfig, load_intelligence_config
from smcsignal.analysis.intelligence.config import (
    DEFAULT_LOSER_WIN_RATE_CEILING,
    DEFAULT_MINIMUM_FINALIZED_FOR_DIAGNOSIS,
    DEFAULT_MINIMUM_FINALIZED_FOR_RANKING,
    DEFAULT_WINNER_WIN_RATE_FLOOR,
    METHODOLOGY_VERSION,
)

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"

GOOD = """\
[intelligence]
enabled = true
minimum_finalized_for_diagnosis = 20
minimum_finalized_for_ranking = 20
winner_win_rate_floor = "0.60"
loser_win_rate_ceiling = "0.40"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "intelligence.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_methodology_and_frozen_defaults_are_safe() -> None:
    assert METHODOLOGY_VERSION == "intelligence-v1"
    defaults = IntelligenceConfig()
    assert defaults.enabled is True
    assert defaults.minimum_finalized_for_diagnosis == 20
    assert defaults.minimum_finalized_for_ranking == 20
    assert defaults.winner_win_rate_floor == Decimal("0.60")
    assert defaults.loser_win_rate_ceiling == Decimal("0.40")
    assert defaults.minimum_finalized_for_ranking >= defaults.minimum_finalized_for_diagnosis


def test_module_default_constants_agree() -> None:
    assert DEFAULT_MINIMUM_FINALIZED_FOR_DIAGNOSIS == 20
    assert DEFAULT_MINIMUM_FINALIZED_FOR_RANKING == 20
    assert DEFAULT_WINNER_WIN_RATE_FLOOR == Decimal("0.60")
    assert DEFAULT_LOSER_WIN_RATE_CEILING == Decimal("0.40")


def test_load_exact_table_round_trips(tmp_path: Path) -> None:
    assert load_intelligence_config(_write(tmp_path, GOOD)) == IntelligenceConfig()


def test_shipped_example_config_loads() -> None:
    assert (
        load_intelligence_config(CONFIG_ROOT / "intelligence.example.toml") == IntelligenceConfig()
    )


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_intelligence_config(_write(tmp_path, GOOD + "extra = 1\n"))


def test_missing_table_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_intelligence_config(_write(tmp_path, "[other]\nx = 1\n"))


def test_ranking_minimum_below_diagnosis_is_rejected() -> None:
    with pytest.raises(AnalysisConfigurationError):
        IntelligenceConfig(minimum_finalized_for_ranking=5)


def test_ceiling_must_stay_below_floor() -> None:
    with pytest.raises(AnalysisConfigurationError):
        IntelligenceConfig(
            winner_win_rate_floor=Decimal("0.40"), loser_win_rate_ceiling=Decimal("0.40")
        )


def test_disabled_intelligence_is_rejected() -> None:
    with pytest.raises(AnalysisConfigurationError):
        IntelligenceConfig(enabled=False)
