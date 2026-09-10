"""Canonical schema, exact numbers, timestamps, ordering, and cleaning policies."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from smcsignal.data import OHLCV_COLUMNS, DataValidationError, normalize_ohlcv


@pytest.mark.parametrize(
    "timestamp",
    [
        "2024-01-01T00:00:00Z",
        "2024-01-01T05:00:00+05:00",
        "2024-01-01T00:00:00.000+00:00",
        1704067200000,
        "1704067200000",
        datetime(2024, 1, 1, 5, tzinfo=timezone(timedelta(hours=5))),
    ],
)
def test_timestamp_formats_normalize_to_utc(raw_row, timestamp) -> None:
    batch = normalize_ohlcv([{**raw_row, "timestamp": timestamp}])
    assert batch.candles[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert batch.candles[0].timestamp.tzinfo is UTC


@pytest.mark.parametrize(
    "timestamp",
    [
        "2024-01-01",
        "2024-01-01T00:00:00",
        datetime(2024, 1, 1),
        "bad",
        -1,
        True,
        1704067200000.0,
        "1704067200000.0",
        10**30,
        "1969-12-31T23:59:59Z",
        "2024-01-01T00:00:00.000001Z",
        [],
    ],
)
def test_invalid_timestamps_are_rejected(raw_row, timestamp) -> None:
    with pytest.raises(DataValidationError, match="row 1: timestamp"):
        normalize_ohlcv([{**raw_row, "timestamp": timestamp}])


def test_prices_are_decimal_without_rounding(raw_row) -> None:
    row = {**raw_row, "open": "100.000000000000000000001", "volume": "1e-20"}
    candle = normalize_ohlcv([row]).candles[0]
    assert candle.open == Decimal("100.000000000000000000001")
    assert candle.volume == Decimal("0.00000000000000000001")
    assert all(isinstance(getattr(candle, field), Decimal) for field in OHLCV_COLUMNS[1:])


def test_finite_python_numbers_are_accepted(raw_row) -> None:
    candle = normalize_ohlcv([{**raw_row, "open": 100, "close": 105.25}]).candles[0]
    assert candle.close == Decimal("105.25")


@pytest.mark.parametrize("field", OHLCV_COLUMNS)
def test_missing_schema_columns_cannot_be_dropped(raw_row, field) -> None:
    raw_row.pop(field)
    with pytest.raises(DataValidationError, match="schema"):
        normalize_ohlcv([raw_row], missing_value_policy="drop")


def test_extra_schema_column_is_rejected(raw_row) -> None:
    with pytest.raises(DataValidationError, match="schema"):
        normalize_ohlcv([{**raw_row, "symbol": "BTCUSDT"}])


@pytest.mark.parametrize("row", [None, [], "timestamp,open,high,low,close,volume", 12])
def test_nonmapping_rows_are_rejected(row) -> None:
    with pytest.raises(DataValidationError, match="schema"):
        normalize_ohlcv([row])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", True),
        ("volume", False),
        ("high", []),
        ("low", {}),
        ("close", "bad"),
        ("volume", "Infinity"),
        ("high", float("inf")),
        ("low", Decimal("-Infinity")),
        ("open", "1_000"),
        ("close", "1,000"),
        ("open", "0"),
        ("high", "0"),
        ("low", "-1"),
        ("close", "-1"),
        ("volume", "-0.01"),
        ("high", "99"),
        ("low", "106"),
        ("open", "120"),
        ("close", "89"),
    ],
)
def test_invalid_numeric_values_and_price_bounds(raw_row, field, value) -> None:
    with pytest.raises(DataValidationError):
        normalize_ohlcv([{**raw_row, field: value}])


def test_flat_candle_and_zero_volume_are_valid() -> None:
    row = dict.fromkeys(OHLCV_COLUMNS[1:], "1")
    row.update(timestamp="2024-01-01T00:00:00Z", volume="0")
    candle = normalize_ohlcv([row]).candles[0]
    assert candle.open == candle.high == candle.low == candle.close
    assert candle.volume == 0


@pytest.mark.parametrize(
    "missing", [None, "", "   ", "NaN", "null", "None", float("nan"), Decimal("NaN")]
)
def test_missing_values_error_by_default(raw_row, missing) -> None:
    with pytest.raises(DataValidationError, match="missing values in volume"):
        normalize_ohlcv([{**raw_row, "volume": missing}])


@pytest.mark.parametrize("field", OHLCV_COLUMNS)
def test_explicit_drop_policy_discards_whole_incomplete_rows(raw_row, field) -> None:
    batch = normalize_ohlcv([{**raw_row, field: None}, raw_row], missing_value_policy="drop")
    assert len(batch.candles) == 1
    assert batch.report.input_rows == 2
    assert batch.report.missing_rows_dropped == 1
    assert batch.report.output_rows == 1


def test_drop_policy_does_not_hide_malformed_nonmissing_values(raw_row) -> None:
    with pytest.raises(DataValidationError, match="open"):
        normalize_ohlcv([{**raw_row, "volume": None, "open": "bad"}], missing_value_policy="drop")


def test_sort_exact_duplicates_and_latest_window_are_audited(raw_row) -> None:
    older = raw_row
    middle = {**raw_row, "timestamp": "2024-01-01T00:15:00Z"}
    newer = {**raw_row, "timestamp": "2024-01-01T00:30:00Z"}
    equivalent = {**middle, "timestamp": "2024-01-01T05:15:00+05:00", "open": 100}
    batch = normalize_ohlcv(
        [newer, older, middle, equivalent, {**raw_row, "close": ""}],
        missing_value_policy="drop",
        history_limit=2,
    )
    assert [c.timestamp.minute for c in batch.candles] == [15, 30]
    assert batch.report.reordered is True
    assert batch.report.input_rows == 5
    assert batch.report.output_rows == 2
    assert batch.report.duplicates_removed == 1
    assert batch.report.missing_rows_dropped == 1
    assert batch.report.rows_trimmed == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("open", "101"), ("high", "111"), ("low", "89"), ("close", "106"), ("volume", "11")],
)
def test_conflicting_duplicates_are_never_silently_resolved(raw_row, field, value) -> None:
    with pytest.raises(DataValidationError, match="conflicting duplicate"):
        normalize_ohlcv([raw_row, {**raw_row, field: value}], history_limit=1)


def test_validation_happens_before_history_truncation(raw_row) -> None:
    with pytest.raises(DataValidationError):
        normalize_ohlcv(
            [{**raw_row, "open": "bad"}, {**raw_row, "timestamp": "2024-01-02T00:00:00Z"}],
            history_limit=1,
        )


def test_gaps_are_not_filled_or_resampled(raw_row) -> None:
    batch = normalize_ohlcv([raw_row, {**raw_row, "timestamp": "2024-01-02T00:00:00Z"}])
    assert len(batch.candles) == 2
    assert batch.report.reordered is False


def test_empty_input_is_an_explicit_empty_batch() -> None:
    batch = normalize_ohlcv([])
    assert batch.candles == ()
    assert batch.report.input_rows == batch.report.output_rows == 0
    assert batch.report.reordered is False


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "5"])
def test_invalid_history_limit(raw_row, limit) -> None:
    with pytest.raises(DataValidationError, match="history_limit"):
        normalize_ohlcv([raw_row], history_limit=limit)


@pytest.mark.parametrize("policy", ["fill", "ignore", None])
def test_invalid_missing_value_policy(raw_row, policy) -> None:
    with pytest.raises(DataValidationError, match="missing_value_policy"):
        normalize_ohlcv([raw_row], missing_value_policy=policy)


def test_input_is_not_mutated_and_output_is_immutable(raw_row) -> None:
    original = raw_row.copy()
    batch = normalize_ohlcv([raw_row])
    assert raw_row == original
    with pytest.raises(FrozenInstanceError):
        batch.candles[0].close = Decimal("1")
    with pytest.raises(FrozenInstanceError):
        batch.report.output_rows = 42


def test_record_serialization_is_lossless_and_canonical(raw_row) -> None:
    candle = normalize_ohlcv([{**raw_row, "timestamp": "2024-01-01T00:00:00.123Z"}]).candles[0]
    record = candle.to_record()
    assert tuple(record) == OHLCV_COLUMNS
    assert record["timestamp"] == "2024-01-01T00:00:00.123Z"
    assert all(isinstance(value, str) for value in record.values())
    assert normalize_ohlcv([record]).candles == (candle,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", 1.0),
        ("volume", Decimal("Infinity")),
        ("timestamp", datetime(2024, 1, 1)),
        ("high", Decimal("1")),
    ],
)
def test_direct_candle_construction_cannot_bypass_invariants(raw_row, field, value) -> None:
    candle = normalize_ohlcv([raw_row]).candles[0]
    with pytest.raises(DataValidationError):
        replace(candle, **{field: value})


@pytest.mark.parametrize(
    "updates",
    [
        {"open": "-1", "volume": None},
        {"high": "1", "volume": None},
        {"low": "120", "close": None},
        {"timestamp": "2024-01-01T00:00:00.000001Z", "volume": None},
    ],
)
def test_drop_policy_cannot_hide_other_checkable_invariants(raw_row, updates) -> None:
    with pytest.raises(DataValidationError):
        normalize_ohlcv([{**raw_row, **updates}], missing_value_policy="drop")


@pytest.mark.parametrize("rows", [None, 42])
def test_noniterable_input_is_actionable(rows) -> None:
    with pytest.raises(DataValidationError, match="iterable"):
        normalize_ohlcv(rows)
