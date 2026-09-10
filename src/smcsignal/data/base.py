"""Provider contract shared by all Phase 2 data sources."""

from abc import ABC, abstractmethod

from smcsignal.data.config import MarketDataConfig
from smcsignal.data.models import OHLCVBatch


class DataProvider(ABC):
    """A single configured OHLCV series; construction must not fetch data."""

    def __init__(self, config: MarketDataConfig) -> None:
        self._config = config

    @property
    def config(self) -> MarketDataConfig:
        """Read-only access to the immutable configuration bound to this series."""
        return self._config

    @abstractmethod
    def fetch_ohlcv(self) -> OHLCVBatch:
        """Return up to history_limit validated candles, ascending and unique.

        Fetching is explicit. Invalid data raises a MarketDataError; providers
        never substitute synthetic data or silently switch to another source.
        """
        raise NotImplementedError
