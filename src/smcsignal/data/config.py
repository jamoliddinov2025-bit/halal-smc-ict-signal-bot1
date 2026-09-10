"""Validated market data settings and explicit TOML loading."""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal

from smcsignal.data.errors import DataConfigurationError
from smcsignal.data.validation import MissingValuePolicy

DataSource = Literal["csv", "binance_public"]
SUPPORTED_TIMEFRAMES = (
    "1s",
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
)


@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    """Bind one provider to one declared symbol/timeframe and history window."""

    symbol: str
    timeframe: str
    data_source: DataSource
    history_limit: int
    csv_path: Path | str | None = None
    missing_value_policy: MissingValuePolicy = "error"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not re.fullmatch(r"[A-Za-z0-9]{2,30}", self.symbol):
            raise DataConfigurationError(
                "symbol must be an exchange symbol such as BTCUSDT (2–30 alphanumeric characters)"
            )
        object.__setattr__(self, "symbol", self.symbol.upper())
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            raise DataConfigurationError(
                f"unsupported timeframe; choose from {', '.join(SUPPORTED_TIMEFRAMES)}"
            )
        if self.data_source not in ("csv", "binance_public"):
            raise DataConfigurationError("data_source must be 'csv' or 'binance_public'")
        if type(self.history_limit) is not int or not 1 <= self.history_limit <= 1000:
            raise DataConfigurationError("history_limit must be an integer from 1 to 1000")
        if self.missing_value_policy not in ("error", "drop"):
            raise DataConfigurationError("missing_value_policy must be 'error' or 'drop'")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 60
            or not math.isfinite(self.timeout_seconds)
        ):
            raise DataConfigurationError(
                "timeout_seconds must be finite and greater than 0, at most 60"
            )
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if self.data_source == "csv":
            if (
                not isinstance(self.csv_path, (str, Path))
                or not str(self.csv_path).strip()
                or "\0" in str(self.csv_path)
            ):
                raise DataConfigurationError("csv_path is required for the CSV data source")
            try:
                object.__setattr__(self, "csv_path", Path(self.csv_path).resolve())
            except (OSError, RuntimeError, ValueError) as exc:
                raise DataConfigurationError("csv_path could not be resolved") from exc
        elif self.csv_path is not None:
            raise DataConfigurationError("csv_path is only valid for the CSV data source")


def load_data_config(path: str | Path) -> MarketDataConfig:
    """Load only [market_data]; relative CSV paths resolve against the TOML file."""
    try:
        config_path = Path(path).resolve()
        with config_path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        raise DataConfigurationError(f"cannot load market data configuration: {exc}") from exc
    table = document.get("market_data")
    if not isinstance(table, dict):
        raise DataConfigurationError("configuration requires a [market_data] table")
    allowed = {field.name for field in fields(MarketDataConfig)}
    unknown = set(table) - allowed
    required = {"symbol", "timeframe", "data_source", "history_limit"}
    missing = required - set(table)
    if unknown:
        raise DataConfigurationError(f"unknown market_data settings: {', '.join(sorted(unknown))}")
    if missing:
        raise DataConfigurationError(f"missing market_data settings: {', '.join(sorted(missing))}")
    values = dict(table)
    if "csv_path" in values:
        raw_path = values["csv_path"]
        if not isinstance(raw_path, str) or not raw_path.strip() or "\0" in raw_path:
            raise DataConfigurationError("csv_path must be a nonempty path string")
        values["csv_path"] = config_path.parent / raw_path
    return MarketDataConfig(**values)
