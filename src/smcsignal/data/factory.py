"""Explicit provider selection; no implicit network calls or source fallback."""

from smcsignal.data.base import DataProvider
from smcsignal.data.binance import BinancePublicDataProvider
from smcsignal.data.config import MarketDataConfig
from smcsignal.data.csv import CsvDataProvider


def create_data_provider(config: MarketDataConfig) -> DataProvider:
    """Construct the provider selected by validated settings; do not fetch."""
    if config.data_source == "csv":
        return CsvDataProvider(config)
    return BinancePublicDataProvider(config)
