"""Phase 2 public API: validated, read-only market data and CSV replay."""

from smcsignal.data.base import DataProvider
from smcsignal.data.binance import BinancePublicDataProvider
from smcsignal.data.config import SUPPORTED_TIMEFRAMES, MarketDataConfig, load_data_config
from smcsignal.data.csv import CsvDataProvider
from smcsignal.data.errors import (
    DataConfigurationError,
    DataProviderError,
    DataValidationError,
    MarketDataError,
    ProviderHTTPError,
    RateLimitError,
)
from smcsignal.data.factory import create_data_provider
from smcsignal.data.models import OHLCV, OHLCV_COLUMNS, OHLCVBatch, ValidationReport
from smcsignal.data.validation import normalize_ohlcv

__all__ = [
    "OHLCV",
    "OHLCV_COLUMNS",
    "SUPPORTED_TIMEFRAMES",
    "BinancePublicDataProvider",
    "CsvDataProvider",
    "DataConfigurationError",
    "DataProvider",
    "DataProviderError",
    "DataValidationError",
    "MarketDataConfig",
    "MarketDataError",
    "OHLCVBatch",
    "ProviderHTTPError",
    "RateLimitError",
    "ValidationReport",
    "create_data_provider",
    "load_data_config",
    "normalize_ohlcv",
]
