"""Replay dataset, step, result, row, and report model validation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis.backtest import (
    BacktestReport,
    BacktestSignalResult,
    ReplayDataset,
    ReplayStep,
    run_backtest,
)
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking import OutcomeStatus
from smcsignal.analysis.signal_engine import SignalDirection
from smcsignal.data import OHLCV
from tests.backtest.helpers import bar_at, bars, configuration, dataset, report


def test_dataset_is_immutable_with_frozen_higher_mapping() -> None:
    instance = dataset()
    with pytest.raises(AttributeError):
        instance.symbol = "ETHUSDT"  # type: ignore[misc]
    with pytest.raises(TypeError):
        instance.higher_candles["1h"] = ()  # type: ignore[index]
    assert instance.higher_candles.keys() == {"1h", "4h"}


@pytest.mark.parametrize("field", ["symbol", "timeframe", "venue", "provider", "dataset_id"])
def test_dataset_rejects_empty_or_padded_text(field: str) -> None:
    candles = bars("15m", (20, 21))
    base = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "candles": candles,
        "higher_candles": {},
        "venue": "synthetic_spot",
        "provider": "csv",
        "dataset_id": "phase20:v1",
    }
    base[field] = "  "
    with pytest.raises(AnalysisInputError, match=field):
        ReplayDataset(**base)  # type: ignore[arg-type]


def test_dataset_rejects_unsupported_timeframe() -> None:
    candles = bars("15m", (20, 21))
    with pytest.raises(AnalysisInputError, match=r"timeframe must be one of"):
        ReplayDataset("BTCUSDT", "47m", candles, {})


def test_dataset_rejects_higher_key_equal_to_primary() -> None:
    with pytest.raises(AnalysisInputError, match=r"cannot equal the primary"):
        dataset(higher={"15m": bars("15m", (20, 21))})


def test_dataset_rejects_invalid_higher_timeframe_key() -> None:
    with pytest.raises(Exception, match=r"timeframe"):
        dataset(higher={"13m": bars("13m", (20, 21))})


def test_dataset_rejects_non_multiple_higher_timeframe() -> None:
    candles = bars("6h", (20, 21))
    with pytest.raises(Exception, match=r"not a strictly higher integer multiple"):
        ReplayDataset("BTCUSDT", "6h", candles, {"8h": bars("8h", (20, 21))})


def test_dataset_rejects_empty_candles() -> None:
    with pytest.raises(AnalysisInputError, match=r"candles must contain at least one candle"):
        dataset(prices=())


def test_dataset_rejects_non_tuple_candles() -> None:
    candles = list(bars("15m", (20, 21)))
    with pytest.raises(AnalysisInputError, match=r"must be a tuple of OHLCV"):
        ReplayDataset("BTCUSDT", "15m", candles)  # type: ignore[arg-type]


def test_dataset_rejects_non_ohlcv_entries() -> None:
    with pytest.raises(AnalysisInputError, match=r"must contain OHLCV candles"):
        ReplayDataset("BTCUSDT", "15m", (bar_at(bars("15m", (20,))[0].timestamp, 20), "candle"))


def test_dataset_rejects_non_chronological_candles() -> None:
    first, second = bars("15m", (20, 21))
    with pytest.raises(AnalysisInputError, match=r"strictly chronological and unique"):
        ReplayDataset("BTCUSDT", "15m", (second, first))


def test_dataset_rejects_overlapping_candles() -> None:
    base = bars("15m", (20,))[0].timestamp
    with pytest.raises(AnalysisInputError, match=r"must not overlap"):
        ReplayDataset(
            "BTCUSDT", "15m", (bar_at(base, 20), bar_at(base + timedelta(minutes=10), 21))
        )


def test_dataset_allows_empty_higher_histories() -> None:
    instance = dataset(higher={"1h": (), "4h": ()})
    assert instance.higher_candles == {"1h": (), "4h": ()}


def test_replay_step_frames_must_share_one_signal_frame() -> None:
    result = report().replays[0]
    first, second = result.steps[0], result.steps[1]
    with pytest.raises(AnalysisInputError, match=r"same signal frame"):
        ReplayStep(0, first.signal, second.outcome, second.attribution)


def test_replay_step_rejects_negative_index() -> None:
    step = report().replays[0].steps[0]
    with pytest.raises(AnalysisInputError, match=r"nonnegative integer"):
        ReplayStep(-1, step.signal, step.outcome, step.attribution)


def test_replay_result_rejects_gapped_steps() -> None:
    result = report().replays[0]
    with pytest.raises(AnalysisInputError, match=r"consecutive order"):
        replace(result, steps=result.steps[1:])


def test_replay_result_rejects_wrong_id_prefix() -> None:
    result = report().replays[0]
    with pytest.raises(AnalysisInputError, match=r"backtest-replay prefix"):
        replace(result, replay_id="other:" + result.replay_id)


def test_row_rejects_non_long_direction() -> None:
    row = report().signals[0]
    with pytest.raises(AnalysisInputError, match=r"LONG"):
        replace(row, direction=SignalDirection.NONE)


def test_row_open_outcomes_carry_no_final_values() -> None:
    open_row = next(row for row in report().signals if not row.finalized)
    with pytest.raises(AnalysisInputError, match=r"final_close must be None"):
        replace(open_row, final_close=Decimal("30"))


def test_row_finalized_outcomes_require_final_values() -> None:
    finalized = next(row for row in report().signals if row.finalized)
    with pytest.raises(AnalysisInputError, match=r"final_index exists"):
        replace(finalized, final_index=None)
    with pytest.raises(AnalysisInputError, match=r"final_return exists"):
        replace(finalized, final_return=None)
    with pytest.raises(AnalysisInputError, match=r"carry MFE and MAE"):
        replace(finalized, mfe_return=None)


def test_row_rejects_nonpositive_reference_close() -> None:
    row = report().signals[0]
    with pytest.raises(AnalysisInputError, match=r"positive Decimal"):
        replace(row, reference_close=Decimal("0"))


def test_row_rejects_out_of_range_score() -> None:
    row = report().signals[0]
    with pytest.raises(AnalysisInputError, match=r"score_total"):
        replace(row, score_total=101)


def test_row_exposes_finalized_flag() -> None:
    rows = report().signals
    assert [row.finalized for row in rows] == [True, False, False, False]
    assert rows[0].outcome_status is OutcomeStatus.WIN


def test_report_rejects_unsorted_rows() -> None:
    instance = report()
    reordered = tuple(reversed(instance.signals))
    with pytest.raises(AnalysisInputError, match=r"sorted by opened_at"):
        replace(instance, signals=reordered)


def test_report_rejects_row_count_mismatch() -> None:
    instance = report()
    with pytest.raises(AnalysisInputError, match=r"partition the overall"):
        replace(instance, signals=instance.signals[:3])


def test_report_rejects_duplicate_replays() -> None:
    with pytest.raises(AnalysisInputError, match=r"exactly once"):
        run_backtest([dataset(), dataset()], configuration())


def test_report_rejects_mismatched_series_keys() -> None:
    instance = report()
    other = replace(instance.performance, series_keys=("x",))
    with pytest.raises(AnalysisInputError, match=r"series keys must match"):
        replace(instance, performance=other)


def test_report_requires_performance_report() -> None:
    instance = report()
    with pytest.raises(AnalysisInputError, match=r"PerformanceReport"):
        replace(instance, performance=instance.performance.overall)  # type: ignore[arg-type]


def test_report_id_uses_backtest_prefix() -> None:
    instance = report()
    with pytest.raises(AnalysisInputError, match=r"backtest prefix"):
        replace(instance, backtest_id="replay:" + instance.backtest_id)


def test_report_row_horizon_matches_configuration() -> None:
    from smcsignal.analysis.outcome_tracking import OutcomeTrackingConfig

    instance = report()
    open_row = next(row for row in reversed(instance.signals) if not row.finalized)
    outcome = replace(
        open_row.outcome,
        settings=OutcomeTrackingConfig(horizon_bars=5),
        horizon_bars=5,
    )
    mismatched = replace(open_row, outcome=outcome)
    with pytest.raises(AnalysisInputError, match=r"configured outcome horizon"):
        replace(instance, signals=(*instance.signals[:-1], mismatched))


def test_backtest_signal_result_is_exported_and_immutable() -> None:
    assert BacktestSignalResult is not None
    row = report().signals[0]
    with pytest.raises(AttributeError):
        row.score_total = 99  # type: ignore[misc]


def test_backtest_report_is_exported_and_immutable() -> None:
    assert BacktestReport is not None
    instance = report()
    with pytest.raises(AttributeError):
        instance.backtest_id = "x"  # type: ignore[misc]


def test_ohlcv_validation_still_applies_inside_datasets() -> None:
    from smcsignal.data.errors import DataValidationError

    with pytest.raises(DataValidationError):
        OHLCV(
            bars("15m", (20,))[0].timestamp,
            Decimal("21"),
            Decimal("20"),
            Decimal("19"),
            Decimal("20"),
            Decimal("1"),
        )
