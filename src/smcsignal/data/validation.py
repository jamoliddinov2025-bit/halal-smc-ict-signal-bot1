"""Strict schema validation, normalization, deduplication, and windowing."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from smcsignal.data.errors import DataValidationError
from smcsignal.data.models import (
    EPOCH,
    OHLCV,
    OHLCV_COLUMNS,
    OHLCVBatch,
    ValidationReport,
    _validate_prices,
)

MissingValuePolicy = Literal["error", "drop"]
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def parse_timestamp(value: object) -> datetime:
    """Parse aware ISO-8601/datetime or integer epoch milliseconds; never guess units."""
    try:
        if isinstance(value, datetime):
            timestamp = value
        elif type(value) is int:
            timestamp = EPOCH + timedelta(milliseconds=value)
        elif isinstance(value, str):
            text = value.strip()
            if re.fullmatch(r"[0-9]+", text):
                timestamp = EPOCH + timedelta(milliseconds=int(text))
            else:
                timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            raise DataValidationError("timestamp must be ISO-8601 or integer epoch milliseconds")
        if timestamp.utcoffset() is None:
            raise DataValidationError(
                "timestamp must include a timezone; naive timestamps are rejected"
            )
        timestamp = timestamp.astimezone(UTC)
        if timestamp < EPOCH:
            raise DataValidationError("timestamp must not precede the Unix epoch")
        if timestamp.microsecond % 1000:
            raise DataValidationError("timestamp must have millisecond precision")
        return timestamp
    except (ValueError, OverflowError) as exc:
        if isinstance(exc, DataValidationError):
            raise
        raise DataValidationError(
            "timestamp is invalid or outside the supported datetime range"
        ) from exc


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "nan", "null", "none"}
    if isinstance(value, float):
        return math.isnan(value)
    return isinstance(value, Decimal) and value.is_nan()


def _parse_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise DataValidationError(f"{field} must be a numeric value, not {type(value).__name__}")
    text = str(value).strip()
    if not _DECIMAL.fullmatch(text):
        raise DataValidationError(f"{field} must be a finite decimal number")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise DataValidationError(f"{field} is not a valid decimal number") from exc
    if not result.is_finite():
        raise DataValidationError(f"{field} must be finite")
    return result


def _parse_row(row: Mapping[str, object], policy: MissingValuePolicy) -> OHLCV | None:
    if not isinstance(row, Mapping) or set(row) != set(OHLCV_COLUMNS):
        raise DataValidationError(f"schema must contain exactly {', '.join(OHLCV_COLUMNS)}")
    missing = [field for field in OHLCV_COLUMNS if _is_missing(row[field])]
    # Validate every nonmissing field before allowing an explicit missing-row drop.
    timestamp = None if "timestamp" in missing else parse_timestamp(row["timestamp"])
    numbers = {
        field: _parse_decimal(row[field], field)
        for field in OHLCV_COLUMNS[1:]
        if field not in missing
    }
    _validate_prices(numbers)
    if missing:
        if policy == "error":
            raise DataValidationError(f"missing values in {', '.join(missing)}")
        return None
    assert timestamp is not None  # all six fields are present and parsed here
    return OHLCV(timestamp=timestamp, **numbers)


def normalize_ohlcv(
    rows: Iterable[Mapping[str, object]],
    *,
    missing_value_policy: MissingValuePolicy = "error",
    history_limit: int | None = None,
) -> OHLCVBatch:
    """Validate all input before returning the latest N rows, oldest first.

    Identical duplicates are removed after type/timezone normalization. A changed
    value at an existing timestamp is an error, never an arbitrary keep-first/last.
    No interpolation, forward filling, aggregation, or fabricated candles occurs.
    """
    if missing_value_policy not in ("error", "drop"):
        raise DataValidationError("missing_value_policy must be 'error' or 'drop'")
    if history_limit is not None and (type(history_limit) is not int or history_limit < 1):
        raise DataValidationError("history_limit must be a positive integer or None")
    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise DataValidationError("rows must be an iterable of OHLCV mappings") from exc
    unique: dict[datetime, OHLCV] = {}
    count = duplicates = dropped = 0
    for count, row in enumerate(iterator, start=1):
        try:
            candle = _parse_row(row, missing_value_policy)
        except DataValidationError as exc:
            raise DataValidationError(f"row {count}: {exc}") from exc
        if candle is None:
            dropped += 1
            continue
        if candle.timestamp in unique:
            if unique[candle.timestamp] != candle:
                raise DataValidationError(
                    f"row {count}: conflicting duplicate timestamp {candle.timestamp.isoformat()}"
                )
            duplicates += 1
        else:
            unique[candle.timestamp] = candle
    ordered = tuple(sorted(unique.values(), key=lambda candle: candle.timestamp))
    reordered = tuple(unique.values()) != ordered
    candles = ordered if history_limit is None else ordered[-history_limit:]
    return OHLCVBatch(
        candles=candles,
        report=ValidationReport(
            input_rows=count,
            output_rows=len(candles),
            duplicates_removed=duplicates,
            missing_rows_dropped=dropped,
            rows_trimmed=len(ordered) - len(candles),
            reordered=reordered,
        ),
    )
