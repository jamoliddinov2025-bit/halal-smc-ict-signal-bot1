"""Immutable canonical OHLCV records and auditable normalization results."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from smcsignal.data.errors import DataValidationError

OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _validate_prices(values: Mapping[str, Decimal]) -> None:
    """Enforce all invariants that can be checked on present price/volume fields."""
    for field, value in values.items():
        if not isinstance(value, Decimal) or not value.is_finite():
            raise DataValidationError(f"{field} must be a finite Decimal")
        if (field == "volume" and value < 0) or (field != "volume" and value <= 0):
            raise DataValidationError(f"{field} is outside its valid range")
    high, low = values.get("high"), values.get("low")
    if high is not None and any(
        high < values[field] for field in ("open", "low", "close") if field in values
    ):
        raise DataValidationError("high must be at least open, low, and close")
    if low is not None and any(
        low > values[field] for field in ("open", "high", "close") if field in values
    ):
        raise DataValidationError("low must be at most open, high, and close")


@dataclass(frozen=True, slots=True)
class OHLCV:
    """One candle: UTC opening time, exact decimal prices, base-asset volume."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime) or self.timestamp.utcoffset() is None:
            raise DataValidationError("timestamp must be a timezone-aware datetime")
        try:
            timestamp = self.timestamp.astimezone(UTC)
        except (ValueError, OverflowError) as exc:
            raise DataValidationError("timestamp is outside the supported datetime range") from exc
        if timestamp < EPOCH or timestamp.microsecond % 1000:
            raise DataValidationError(
                "timestamp must be nonnegative epoch time at millisecond precision"
            )
        object.__setattr__(self, "timestamp", timestamp)
        _validate_prices({field: getattr(self, field) for field in OHLCV_COLUMNS[1:]})

    def to_record(self) -> dict[str, str]:
        """Return the six canonical columns as lossless JSON/CSV-safe strings."""
        return {
            "timestamp": self.timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            **{field: str(getattr(self, field)) for field in OHLCV_COLUMNS[1:]},
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Counts partition input rows; output_rows is the returned window size."""

    input_rows: int
    output_rows: int
    duplicates_removed: int = 0
    missing_rows_dropped: int = 0
    rows_trimmed: int = 0
    incomplete_rows_dropped: int = 0
    reordered: bool = False


@dataclass(frozen=True, slots=True)
class OHLCVBatch:
    """An ascending, unique candle snapshot plus its cleaning audit."""

    candles: tuple[OHLCV, ...]
    report: ValidationReport
