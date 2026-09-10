"""Actionable, provider-independent market data exceptions."""


class MarketDataError(Exception):
    """Base exception for the market data foundation."""


class DataConfigurationError(MarketDataError, ValueError):
    """An unsupported or malformed market data configuration."""


class DataValidationError(MarketDataError, ValueError):
    """Input data violates the declared OHLCV contract."""


class DataProviderError(MarketDataError):
    """A file, transport, or upstream response could not be read."""


class ProviderHTTPError(DataProviderError):
    """HTTP failure with status and Retry-After retained for the caller."""

    def __init__(self, status_code: int, retry_after: str | None = None) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"Binance public data request failed with HTTP {status_code}")


class RateLimitError(ProviderHTTPError):
    """HTTP 429/418: stop requests and respect upstream backoff; no auto-retry."""
