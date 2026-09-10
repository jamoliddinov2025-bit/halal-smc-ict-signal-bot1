"""Evidence artifact, configuration hash, and report identity tests."""

from __future__ import annotations

import json
import re

from smcsignal.analysis.robustness import evaluate_dataset
from smcsignal.analysis.robustness.evidence import (
    configuration_artifact,
    configuration_hash,
    robustness_identity,
    series_key_of,
)
from tests.robustness.helpers import configuration, rich_dataset, robustness

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def result_for(symbol: str = "BTCUSDT"):
    return evaluate_dataset(rich_dataset(symbol=symbol), configuration(), robustness())


def test_configuration_artifact_is_deterministic_bytes() -> None:
    config = robustness()
    assert configuration_artifact(config) == configuration_artifact(config)
    assert isinstance(configuration_artifact(config), bytes)


def test_configuration_artifact_declares_the_validation_only_role() -> None:
    payload = json.loads(configuration_artifact(robustness()))
    assert payload["methodology"] == "robustness-v1"
    assert payload["role"] == "robustness_validation_only"
    # every capability this phase must not have is explicitly disclaimed
    for flag in (
        "optimization",
        "parameter_selection",
        "strategy_modification",
        "self_modification",
        "signal_generation",
        "signal_veto",
        "live_trading",
        "execution",
        "advice",
        "statistical_significance_claim",
    ):
        assert payload[flag] is False, flag
    # the frozen settings themselves are embedded
    assert payload["settings"]["development_bars"] == 12
    assert payload["settings"]["validation_bars"] == 14


def test_configuration_artifact_is_sensitive_to_settings() -> None:
    base = configuration_artifact(robustness())
    changed = configuration_artifact(robustness(step_bars=15))
    assert base != changed


def test_configuration_hash_is_a_deterministic_sha256() -> None:
    config = robustness()
    first = configuration_hash(config)
    assert SHA256.match(first)
    assert first == configuration_hash(config)
    assert first != configuration_hash(robustness(development_bars=13))


def test_robustness_identity_is_deterministic_and_prefixed() -> None:
    results = [result_for()]
    first = robustness_identity(configuration(), robustness(), results)
    assert first.startswith("robustness-report:")
    assert SHA256.match(first.removeprefix("robustness-report:"))
    repeat = robustness_identity(configuration(), robustness(), [result_for()])
    assert first == repeat


def test_robustness_identity_is_sensitive_to_the_settings() -> None:
    results = [result_for()]
    baseline = robustness_identity(configuration(), robustness(), results)
    different = robustness_identity(
        configuration(), robustness(minimum_finalized_for_stability=2), results
    )
    assert baseline != different


def test_robustness_identity_is_sensitive_to_the_dataset() -> None:
    baseline = robustness_identity(configuration(), robustness(), [result_for("BTCUSDT")])
    other_symbol = robustness_identity(configuration(), robustness(), [result_for("ETHUSDT")])
    assert baseline != other_symbol


def test_report_id_equals_the_robustness_identity_of_its_datasets() -> None:
    from smcsignal.analysis.robustness import run_robustness

    datasets = [rich_dataset()]
    report = run_robustness(datasets, configuration(), robustness())
    expected = robustness_identity(configuration(), robustness(), list(report.datasets))
    assert report.report_id == expected


def test_series_key_of_matches_the_phase20_series_key() -> None:
    from smcsignal.analysis.backtest.calculation import series_key_for

    result = result_for()
    assert series_key_of(result) == series_key_for(result.dataset)
    assert series_key_of(result).startswith("BTCUSDT:15m:")
