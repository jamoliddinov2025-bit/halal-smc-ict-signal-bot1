"""Strict local CSV ingestion and deterministic, unpaced candle replay."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from smcsignal.data.base import DataProvider
from smcsignal.data.config import MarketDataConfig
from smcsignal.data.errors import DataConfigurationError, DataProviderError, DataValidationError
from smcsignal.data.models import OHLCV, OHLCV_COLUMNS, OHLCVBatch
from smcsignal.data.validation import normalize_ohlcv


class CsvDataProvider(DataProvider):
    """Read a caller-declared, single-symbol/timeframe file without network I/O."""

    def __init__(self, config: MarketDataConfig) -> None:
        if config.data_source != "csv":
            raise DataConfigurationError("CsvDataProvider requires data_source='csv'")
        super().__init__(config)

    def fetch_ohlcv(self) -> OHLCVBatch:
        """Validate the whole file before selecting the latest history window."""
        path = self.config.csv_path
        assert isinstance(path, Path)  # validated and resolved by MarketDataConfig
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream, strict=True)
                header = next(reader, None)
                if header is None or len(header) != 6 or set(header) != set(OHLCV_COLUMNS):
                    raise DataValidationError(
                        "CSV header must contain each canonical OHLCV column exactly once"
                    )

                def rows() -> Iterator[dict[str, object]]:
                    for row in reader:
                        if not row:
                            yield dict.fromkeys(header)
                        elif len(row) != len(header):
                            raise DataValidationError(
                                f"CSV line {reader.line_num}: expected six fields"
                            )
                        else:
                            yield dict(zip(header, row, strict=True))

                return normalize_ohlcv(
                    rows(),
                    missing_value_policy=self.config.missing_value_policy,
                    history_limit=self.config.history_limit,
                )
        except DataValidationError:
            raise
        except (csv.Error, UnicodeError) as exc:
            raise DataValidationError(f"CSV must be well-formed UTF-8: {exc}") from exc
        except OSError as exc:
            raise DataProviderError(f"cannot read CSV file {self.config.csv_path}: {exc}") from exc

    def replay(self) -> Iterator[OHLCV]:
        """Replay an eagerly validated snapshot oldest-first, without sleeping.

        Each call creates an independent snapshot of the same latest-N window
        returned by fetch_ohlcv. This is an iterator, not a backtesting engine.
        """
        return iter(self.fetch_ohlcv().candles)
