"""Robustness report rendering and deterministic machine summary tests."""

from __future__ import annotations

import json

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.robustness import (
    machine_summary,
    render_robustness_text,
    run_robustness,
    summary_payload,
)
from tests.robustness.helpers import EIGHT, configuration, rich_dataset, robustness

EXPECTED_TEXT = """SMC/ICT signal bot robustness report (robustness-v1)
walk-forward validation over the unchanged pipeline; not advice, not optimization
generated 2024-01-01T08:00:00Z
report robustness-report:4b30cc6f26d8a2b0260ae4ba086c4c58254d738282ff42b2e71fc6d18437b818

dataset BTCUSDT 15m candles 60 windows 3

overall all
  signals 16, open 12, finalized 4 (win 1, loss 0, flat 3)
  win_rate 0.25

windows:
  window 0 development period 2024-01 regime TRENDING status n/a
    signals 2, open 0, finalized 2 (win 2, loss 0, flat 0)
    win_rate 1
    average_final_return 0.38690476190476190476190476190476190476190476190476
  window 0 validation period 2024-01 regime TRENDING status STABLE
    signals 2, open 1, finalized 1 (win 1, loss 0, flat 0)
    win_rate 1
    average_final_return 0.3125
    degradation win_rate 0
    degradation average_final_return -0.07440476190476190476190476190476190476190476190476
  window 1 development period 2024-01 regime TRENDING status n/a
    signals 2, open 0, finalized 2 (win 2, loss 0, flat 0)
    win_rate 1
    average_final_return 0.28594771241830065359477124183006535947712418300654
  window 1 validation period 2024-01 regime TRENDING status UNDERSAMPLED
    signals 5, open 5, finalized 0 (win 0, loss 0, flat 0)
    win_rate undefined
    average_final_return undefined
    degradation win_rate undefined
    degradation average_final_return undefined
  window 2 development period 2024-01 regime TRENDING status n/a
    signals 6, open 0, finalized 6 (win 0, loss 1, flat 5)
    win_rate 0
    average_final_return -0.072916666666666666666666666666666666666666666666667
  window 2 validation period 2024-01 regime TRENDING status WEAK
    signals 9, open 6, finalized 3 (win 0, loss 0, flat 3)
    win_rate 0
    average_final_return 0
    degradation win_rate 0
    degradation average_final_return 0.072916666666666666666666666666666666666666666666667

by symbol:
  symbol BTCUSDT
    signals 16, open 12, finalized 4 (win 1, loss 0, flat 3)
    win_rate 0.25

by timeframe:
  timeframe 15m
    signals 16, open 12, finalized 4 (win 1, loss 0, flat 3)
    win_rate 0.25

by combination:
  combination fvg_bearish+mtf_bullish
    signals 2, open 2, finalized 0 (win 0, loss 0, flat 0)
    win_rate undefined
  combination fvg_bullish+pd_discount+mtf_bullish
    signals 1, open 1, finalized 0 (win 0, loss 0, flat 0)
    win_rate undefined
  combination fvg_bullish+pd_premium+mtf_bullish
    signals 7, open 5, finalized 2 (win 0, loss 0, flat 2)
    win_rate 0
  combination mtf_bullish
    signals 2, open 1, finalized 1 (win 1, loss 0, flat 0)
    win_rate 1
  combination pd_equilibrium+mtf_bullish
    signals 4, open 3, finalized 1 (win 0, loss 0, flat 1)
    win_rate 0

by period:
  BTCUSDT:15m:synthetic_spot:csv:phase13-fixture:v1
    period 2024-01 regime TRENDING status STABLE
      signals 2, open 1, finalized 1 (win 1, loss 0, flat 0)
      win_rate 1
      average_final_return 0.3125
  BTCUSDT:15m:synthetic_spot:csv:phase13-fixture:v1
    period 2024-01 regime TRENDING status UNDERSAMPLED
      signals 5, open 5, finalized 0 (win 0, loss 0, flat 0)
      win_rate undefined
      average_final_return undefined
  BTCUSDT:15m:synthetic_spot:csv:phase13-fixture:v1
    period 2024-01 regime TRENDING status WEAK
      signals 9, open 6, finalized 3 (win 0, loss 0, flat 3)
      win_rate 0
      average_final_return 0

by regime:
  regime TRENDING status WEAK
    signals 16, open 12, finalized 4 (win 1, loss 0, flat 3)
    win_rate 0.25
    average_final_return 0.078125

degradation: windows 3 comparable 2
  average_win_rate 0
  average_final_return -0.0007440476190476190476190476165

stability: segments 3 sufficient 2 positive 1 negative 0
  win_rate_spread 1
  average_final_return_spread 0.3125
  average_mfe_return_spread 0.273936097427476737821565407772304324028461959496441
  average_mae_return_spread 0.13724685276409414340448823207443897099069512862617
  best period 2024-01 worst period 2024-01
  status WEAK
"""


def build_report():
    return run_robustness([rich_dataset()], configuration(), robustness())


def test_report_text_matches_the_golden_rendering() -> None:
    report = build_report()
    text = render_robustness_text(report, generated_at=EIGHT)
    assert text == EXPECTED_TEXT


def test_report_text_is_deterministic_across_runs() -> None:
    first = render_robustness_text(build_report(), generated_at=EIGHT)
    second = render_robustness_text(build_report(), generated_at=EIGHT)
    assert first == second


def test_report_text_changes_only_with_the_generation_instant() -> None:
    from datetime import timedelta

    report = build_report()
    first = render_robustness_text(report, generated_at=EIGHT)
    later = render_robustness_text(report, generated_at=EIGHT + timedelta(minutes=15))
    assert first != later
    assert first.splitlines()[2].endswith("T08:00:00Z")
    assert later.splitlines()[2].endswith("T08:15:00Z")
    assert first.splitlines()[3:] == later.splitlines()[3:]


def test_render_requires_the_generation_instant_to_be_utc_aware() -> None:
    from datetime import datetime

    with pytest.raises(AnalysisInputError, match=r"UTC"):
        render_robustness_text(build_report(), generated_at=datetime(2024, 1, 1))
    with pytest.raises(AnalysisInputError):
        render_robustness_text("report")  # type: ignore[arg-type]


def test_machine_summary_is_byte_identical_across_runs() -> None:
    first = machine_summary(build_report())
    second = machine_summary(build_report())
    assert first == second
    assert first == machine_summary(build_report())


def test_machine_summary_is_valid_canonical_json() -> None:
    payload = json.loads(machine_summary(build_report()))
    assert payload["methodology"] == "robustness-v1"
    assert payload["report_id"].startswith("robustness-report:")
    assert sorted(
        payload,
        key=str,
    ) == sorted(
        [
            "methodology",
            "report_id",
            "configuration_hash",
            "series_keys",
            "overall",
            "by_symbol",
            "by_timeframe",
            "by_combination",
            "by_period",
            "by_regime",
            "windows",
            "degradation",
            "stability",
            "signals",
        ],
        key=str,
    )


def test_machine_summary_serializes_decimals_as_exact_strings() -> None:
    payload = json.loads(machine_summary(build_report()))
    assert isinstance(payload["overall"]["final_return_sum"], str)
    assert payload["overall"]["win_rate"] == "0.25"
    windows = payload["windows"]
    assert windows[0]["validation"]["win_rate"] == "1"
    assert windows[0]["average_final_return_degradation"] == (
        "-0.07440476190476190476190476190476190476190476190476"
    )
    assert windows[1]["win_rate_degradation"] is None


def test_machine_summary_covers_every_validation_signal_with_regime() -> None:
    payload = json.loads(machine_summary(build_report()))
    signals = payload["signals"]
    assert len(signals) == payload["overall"]["total_buy_signals"]
    assert {signal["regime"] for signal in signals} == {"TRENDING"}
    assert all(signal["signal_id"].startswith("signal-candidate:") for signal in signals)


def test_machine_summary_declares_the_validation_only_role() -> None:
    payload = json.loads(machine_summary(build_report()))
    assert payload["stability"]["status"] in ("STABLE", "WEAK", "UNDERSAMPLED")
    assert (
        payload["degradation"]["comparable_window_count"] <= payload["degradation"]["window_count"]
    )
    # no field anywhere promises significance, advice, or execution
    text = machine_summary(build_report()).decode("utf-8")
    for forbidden in ("significance", "p_value", "p-value", "advice", "recommend"):
        assert forbidden not in text


def test_summary_payload_matches_the_machine_bytes() -> None:
    from smcsignal.analysis.liquidity.evidence import canonical_bytes

    report = build_report()
    assert canonical_bytes(summary_payload(report)) == machine_summary(report)


def test_report_id_is_stable_and_derived_from_inputs() -> None:
    first = build_report()
    second = build_report()
    assert first.report_id == second.report_id
    assert first.report_id != second.report_id.replace("robustness", "backtest")
    from smcsignal.analysis.robustness import configuration_hash

    assert configuration_hash(first.settings) == configuration_hash(second.settings)
