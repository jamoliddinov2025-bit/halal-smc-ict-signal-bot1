"""Strict CSV import and deterministic snapshot replay, without network access."""

import csv
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smcsignal.data import (
    OHLCV_COLUMNS,
    CsvDataProvider,
    DataProvider,
    DataProviderError,
    DataValidationError,
    MarketDataConfig,
)


def provider(path, **overrides):
    return CsvDataProvider(
        MarketDataConfig(
            **{
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "data_source": "csv",
                "history_limit": 500,
                "csv_path": path,
                **overrides,
            }
        )
    )


def write_rows(path, rows, header=OHLCV_COLUMNS):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for row in rows:
            writer.writerow([row[key] for key in header])
    return path


def test_fixture_fetches_exact_canonical_values(fixture_csv) -> None:
    source = provider(fixture_csv)
    assert isinstance(source, DataProvider)
    batch = source.fetch_ohlcv()
    assert len(batch.candles) == 5
    assert batch.candles[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert batch.candles[0].open == Decimal("100.00")
    assert batch.candles[0].volume == Decimal("10.50")
    assert batch.candles[2].volume == 0
    assert batch.report.input_rows == batch.report.output_rows == 5


def test_history_window_returns_latest_candles_oldest_first(fixture_csv) -> None:
    batch = provider(fixture_csv, history_limit=2).fetch_ohlcv()
    assert [c.timestamp.strftime("%H:%M") for c in batch.candles] == ["00:45", "01:00"]
    assert batch.report.rows_trimmed == 3


def test_replay_is_repeatable_and_matches_fetch(fixture_csv) -> None:
    source = provider(fixture_csv)
    first, second = source.replay(), source.replay()
    assert first is not second
    assert tuple(first) == tuple(second) == source.fetch_ohlcv().candles


def test_replay_is_an_independent_eager_snapshot(tmp_path, raw_row) -> None:
    path = write_rows(tmp_path / "data.csv", [raw_row])
    source = provider(path)
    replay = source.replay()
    path.write_text("invalid new file contents")
    assert len(tuple(replay)) == 1
    with pytest.raises(DataValidationError):
        source.replay()


def test_csv_sort_and_duplicate_removal(tmp_path, raw_row) -> None:
    newer = {**raw_row, "timestamp": "2024-01-01T00:15:00Z"}
    path = write_rows(tmp_path / "data.csv", [newer, raw_row, raw_row])
    batch = provider(path).fetch_ohlcv()
    assert [c.timestamp.minute for c in batch.candles] == [0, 15]
    assert batch.report.duplicates_removed == 1
    assert batch.report.reordered is True


def test_csv_conflicting_duplicates_raise(tmp_path, raw_row) -> None:
    path = write_rows(tmp_path / "data.csv", [raw_row, {**raw_row, "close": "106"}])
    with pytest.raises(DataValidationError, match="conflicting duplicate"):
        provider(path).fetch_ohlcv()


def test_missing_values_require_explicit_drop_policy(tmp_path, raw_row) -> None:
    path = write_rows(tmp_path / "data.csv", [{**raw_row, "volume": ""}, raw_row])
    with pytest.raises(DataValidationError, match="missing values"):
        provider(path).fetch_ohlcv()
    batch = provider(path, missing_value_policy="drop").fetch_ohlcv()
    assert len(batch.candles) == 1
    assert batch.report.missing_rows_dropped == 1


def test_blank_rows_are_not_silently_skipped(tmp_path) -> None:
    path = tmp_path / "data.csv"
    path.write_text(",".join(OHLCV_COLUMNS) + "\n\n")
    with pytest.raises(DataValidationError, match="missing values"):
        provider(path).fetch_ohlcv()
    batch = provider(path, missing_value_policy="drop").fetch_ohlcv()
    assert batch.report.input_rows == batch.report.missing_rows_dropped == 1


def test_reordered_columns_and_utf8_bom_are_supported(tmp_path, raw_row) -> None:
    path = write_rows(tmp_path / "data.csv", [raw_row], tuple(reversed(OHLCV_COLUMNS)))
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert provider(path).fetch_ohlcv().candles[0].open == 100


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "timestamp,open,high,low,close\n",
        "timestamp,open,high,low,close,close\n",
        "timestamp,open,high,low,close,volume,symbol\n",
        "Timestamp,open,high,low,close,volume\n",
        "timestamp,open,high,low,close,volume\n2024-01-01T00:00:00Z,1,2,1,2\n",
        "timestamp,open,high,low,close,volume\n2024-01-01T00:00:00Z,1,2,1,2,3,4\n",
        'timestamp,open,high,low,close,volume\n"unterminated',
    ],
)
def test_malformed_csv_is_rejected_even_with_drop_policy(tmp_path, contents) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(contents)
    with pytest.raises(DataValidationError):
        provider(path, missing_value_policy="drop").fetch_ohlcv()


def test_non_utf8_csv_is_rejected(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes(b"\xff\xfeinvalid")
    with pytest.raises(DataValidationError, match="UTF-8"):
        provider(path).fetch_ohlcv()


def test_header_only_csv_produces_empty_batch(tmp_path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text(",".join(OHLCV_COLUMNS) + "\n")
    assert provider(path).fetch_ohlcv().candles == ()


def test_missing_file_errors_without_fallback(tmp_path) -> None:
    source = provider(tmp_path / "missing.csv")
    with pytest.raises(DataProviderError, match="cannot read CSV"):
        source.fetch_ohlcv()
