"""Deterministic machine-readable and human-readable backtest summaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smcsignal.analysis.backtest import (
    machine_summary,
    render_backtest_text,
    run_backtest,
    summary_payload,
)
from smcsignal.analysis.errors import AnalysisInputError
from tests.backtest.helpers import GENERATED_AT, configuration, dataset, report

GOLDEN_TEXT = """SMC/ICT signal bot backtest summary (backtest-replay-v1)
historical replay through the unchanged pipeline; not advice, not execution
generated 2024-02-01T00:00:00Z
backtest backtest:fdede9aebe06581a609e3c990b81fe0559bf44610798d708bfe0a575bad7638b

dataset BTCUSDT 15m candles 17; higher 1h 12, 4h 1
replays 1

overall all: signals 4, open 3, finalized 1 (win 1, loss 0, flat 0)
  win_rate 1
  final_return_sum 0.41666666666666666666666666666666666666666666666667
  mfe_return_sum 0.45833333333333333333333333333333333333333333333333
  mae_return_sum 0
  average_final_return 0.41666666666666666666666666666666666666666666666667
  average_mfe_return 0.45833333333333333333333333333333333333333333333333
  average_mae_return 0
  best combination none
  worst combination none

by symbol:
  symbol BTCUSDT: signals 4, open 3, finalized 1 (win 1, loss 0, flat 0)
    win_rate 1
    average_final_return 0.41666666666666666666666666666666666666666666666667

by timeframe:
  timeframe 15m: signals 4, open 3, finalized 1 (win 1, loss 0, flat 0)
    win_rate 1
    average_final_return 0.41666666666666666666666666666666666666666666666667

by label:
  label mtf_bullish: signals 4, open 3, finalized 1 (win 1, loss 0, flat 0)
    win_rate 1
    average_final_return 0.41666666666666666666666666666666666666666666666667

by combination:
  combination mtf_bullish: signals 4, open 3, finalized 1 (win 1, loss 0, flat 0)
    win_rate 1
    average_final_return 0.41666666666666666666666666666666666666666666666667

by month:
  month 2024-01: signals 4, open 3, finalized 1 (win 1, loss 0, flat 0)
    win_rate 1
    average_final_return 0.41666666666666666666666666666666666666666666666667
"""


def test_backtest_text_matches_the_golden_render() -> None:
    assert render_backtest_text(report(), generated_at=GENERATED_AT) == GOLDEN_TEXT


def test_backtest_text_is_deterministic_for_a_fixed_instant() -> None:
    first = render_backtest_text(report(), generated_at=GENERATED_AT)
    second = render_backtest_text(report(), generated_at=GENERATED_AT)
    assert first == second


def test_backtest_text_changes_only_its_timestamp_line() -> None:
    later = datetime(2024, 3, 1, tzinfo=UTC)
    first = render_backtest_text(report(), generated_at=GENERATED_AT).splitlines()
    second = render_backtest_text(report(), generated_at=later).splitlines()
    assert len(first) == len(second)
    differences = [(a, b) for a, b in zip(first, second, strict=True) if a != b]
    assert differences == [("generated 2024-02-01T00:00:00Z", "generated 2024-03-01T00:00:00Z")]


def test_backtest_text_rejects_naive_timestamps() -> None:
    with pytest.raises(AnalysisInputError, match=r"timezone-aware"):
        render_backtest_text(report(), generated_at=datetime(2024, 2, 1))


def test_backtest_text_rejects_non_reports() -> None:
    with pytest.raises(AnalysisInputError, match=r"BacktestReport"):
        render_backtest_text("report")  # type: ignore[arg-type]


def test_zero_signal_text_is_valid_and_deterministic() -> None:
    empty = run_backtest([dataset(prices=(20, 21))], configuration())
    text = render_backtest_text(empty, generated_at=GENERATED_AT)
    assert "signals 0, open 0, finalized 0 (win 0, loss 0, flat 0)" in text
    assert "win_rate undefined" in text
    assert "by symbol: none" in text
    assert "by month: none" in text
    assert text == render_backtest_text(empty, generated_at=GENERATED_AT)


def test_machine_summary_is_stable_canonical_json() -> None:
    instance = report()
    payload = machine_summary(instance)
    assert payload == machine_summary(instance)
    assert payload == machine_summary(report())  # identical backtests serialize identically
    decoded = json.loads(payload.decode("utf-8"))
    assert decoded == summary_payload(instance)


def test_machine_summary_reports_every_required_group() -> None:
    decoded = json.loads(machine_summary(report()).decode("utf-8"))
    assert decoded["methodology"] == "backtest-replay-v1"
    assert decoded["backtest_id"].startswith("backtest:")
    assert decoded["overall"]["total_buy_signals"] == 4
    assert decoded["overall"]["win_rate"] == "1"
    assert [bucket["name"] for bucket in decoded["by_symbol"]] == ["BTCUSDT"]
    assert [bucket["name"] for bucket in decoded["by_timeframe"]] == ["15m"]
    assert [bucket["name"] for bucket in decoded["by_label"]] == ["mtf_bullish"]
    assert [bucket["name"] for bucket in decoded["by_combination"]] == ["mtf_bullish"]
    assert [bucket["name"] for bucket in decoded["by_month"]] == ["2024-01"]
    assert len(decoded["signals"]) == 4
    row = decoded["signals"][0]
    assert row["candle_index"] == 4
    assert row["outcome_status"] == "WIN"
    assert row["final_return"].startswith("0.4166")
    assert row["direction"] == "LONG"
    assert row["labels"] == ["mtf_bullish"]
    assert decoded["datasets"][0]["candles"] == 17


def test_machine_summary_keeps_decimals_exact_as_strings() -> None:
    decoded = json.loads(machine_summary(report()).decode("utf-8"))
    final = decoded["signals"][0]["final_return"]
    assert isinstance(final, str)
    assert Decimal(final) == Decimal("0.41666666666666666666666666666666666666666666666667")


def test_machine_summary_of_multi_dataset_backtests_partitions() -> None:
    result = run_backtest([dataset(), dataset(symbol="ETHUSDT")], configuration())
    decoded = json.loads(machine_summary(result).decode("utf-8"))
    assert len(decoded["datasets"]) == 2
    assert decoded["overall"]["total_buy_signals"] == 8
    assert {bucket["name"] for bucket in decoded["by_symbol"]} == {"BTCUSDT", "ETHUSDT"}


def test_zero_signal_machine_summary_is_valid() -> None:
    empty = run_backtest([dataset(prices=(20, 21))], configuration())
    decoded = json.loads(machine_summary(empty).decode("utf-8"))
    assert decoded["overall"]["total_buy_signals"] == 0
    assert decoded["overall"]["win_rate"] is None
    assert decoded["signals"] == []
    assert decoded["by_symbol"] == []
