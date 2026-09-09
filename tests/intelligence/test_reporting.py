"""Phase 22 reporting: machine summary, canonical JSON, and role declarations."""

from __future__ import annotations

import json

from smcsignal.analysis.intelligence import analyze_report, render_intelligence_text
from smcsignal.analysis.intelligence.evidence import machine_summary, summary_payload
from tests.intelligence.helpers import intelligence, report

EASY = intelligence(minimum_finalized_for_diagnosis=1, minimum_finalized_for_ranking=1)


def test_machine_summary_is_byte_identical_across_runs() -> None:
    source = report()
    first = machine_summary(analyze_report(source, EASY))
    second = machine_summary(analyze_report(source, EASY))
    assert first == second


def test_machine_summary_is_valid_canonical_json() -> None:
    payload = json.loads(machine_summary(analyze_report(report(), EASY)))
    assert payload["methodology"] == "intelligence-v1"
    assert payload["report_id"].startswith("intelligence-report:")
    assert payload["series_keys"] == ["BTCUSDT:15m:synthetic_spot:csv:phase13-fixture:v1"]
    assert set(payload["overall"]) >= {"dimension", "name", "pattern", "diagnostic", "rank"}
    assert sorted(payload, key=str) == sorted(
        [
            "methodology",
            "report_id",
            "configuration_hash",
            "series_keys",
            "role",
            "optimization",
            "parameter_selection",
            "parameter_tuning",
            "automatic_strategy_selection",
            "threshold_tuning",
            "self_modification",
            "feedback_into_signal_generation",
            "automatic_setup_enabling",
            "automatic_setup_disabling",
            "signal_veto",
            "regime_re_detection",
            "advice",
            "phase_23_optimization",
            "overall",
            "by_setup",
            "by_symbol",
            "by_timeframe",
            "by_month",
            "by_regime",
        ],
        key=str,
    )


def test_decimals_serialize_as_exact_strings() -> None:
    payload = json.loads(machine_summary(analyze_report(report(), EASY)))
    assert isinstance(payload["overall"]["final_return_sum"], str)
    assert isinstance(payload["overall"]["win_rate"], str)


def test_machine_summary_declares_the_research_only_role() -> None:
    payload = json.loads(machine_summary(analyze_report(report(), EASY)))
    assert payload["role"] == "strategy_intelligence_research_only"
    for flag in (
        "optimization",
        "parameter_selection",
        "parameter_tuning",
        "automatic_strategy_selection",
        "threshold_tuning",
        "self_modification",
        "feedback_into_signal_generation",
        "automatic_setup_enabling",
        "automatic_setup_disabling",
        "signal_veto",
        "regime_re_detection",
        "advice",
        "phase_23_optimization",
    ):
        assert payload[flag] is False
    text = machine_summary(analyze_report(report(), EASY)).decode("utf-8")
    for forbidden in ("recommend", "significance", "p_value"):
        assert forbidden not in text


def test_summary_payload_matches_machine_bytes() -> None:
    from smcsignal.analysis.liquidity.evidence import canonical_bytes

    analyzed = analyze_report(report(), EASY)
    assert canonical_bytes(summary_payload(analyzed)) == machine_summary(analyzed)


def test_text_render_declares_population_and_role() -> None:
    analyzed = analyze_report(report(), EASY)
    text = render_intelligence_text(analyzed)
    assert text.startswith("Strategy Intelligence report")
    assert "Phase 21 validation rows, each described exactly once." in text
    assert "Observational research only" in text
    assert "Phase 23 optimization is not approved and is never run." in text


def test_text_lists_strength_and_weakness_counts() -> None:
    analyzed = analyze_report(report(), EASY)
    text = render_intelligence_text(analyzed)
    assert "strength diagnostics=" in text
    assert "weakness diagnostics=" in text
