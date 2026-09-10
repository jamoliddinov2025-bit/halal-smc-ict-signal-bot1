"""Read-only Binance public Spot klines; no API keys or account endpoints."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.client import HTTPException
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from smcsignal import __version__
from smcsignal.data.base import DataProvider
from smcsignal.data.config import MarketDataConfig
from smcsignal.data.errors import (
    DataConfigurationError,
    DataProviderError,
    DataValidationError,
    ProviderHTTPError,
    RateLimitError,
)
from smcsignal.data.models import EPOCH, OHLCV_COLUMNS, OHLCVBatch
from smcsignal.data.validation import normalize_ohlcv, parse_timestamp

KLINES_ENDPOINT = "https://data-api.binance.vision/api/v3/klines"
MAX_RESPONSE_BYTES = 2_000_000


class JsonTransport(Protocol):
    """Injectable request boundary for offline provider tests."""

    def __call__(self, url: str, timeout: float) -> object:
        """Return decoded JSON or raise a transport error."""
        ...


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonstandard JSON numeric constant: {value}")


def _get_json(url: str, timeout: float) -> object:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": f"smcsignal/{__version__}"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise DataProviderError("Binance response exceeds the 2 MB safety limit")
    try:
        return json.loads(
            payload.decode("utf-8"), parse_float=Decimal, parse_constant=_reject_json_constant
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DataProviderError("Binance response is not valid UTF-8 JSON") from exc


class BinancePublicDataProvider(DataProvider):
    """Fetch a bounded snapshot of closed Spot candles, with no automatic retry."""

    def __init__(
        self,
        config: MarketDataConfig,
        *,
        transport: JsonTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if config.data_source != "binance_public":
            raise DataConfigurationError(
                "BinancePublicDataProvider requires data_source='binance_public'"
            )
        super().__init__(config)
        self._transport = transport if transport is not None else _get_json
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def fetch_ohlcv(self) -> OHLCVBatch:
        """Read one REST page and exclude candles not closed at request start.

        Request one spare row where possible because the upstream page can contain
        the current candle. At the 1000-row API cap fewer than history_limit may
        remain. No pagination, interpolation, or replacement data is implied.
        """
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise DataProviderError("clock must return a timezone-aware datetime")
        try:
            cutoff_ms = (now.astimezone(UTC) - EPOCH) // timedelta(milliseconds=1)
        except (ValueError, OverflowError) as exc:
            raise DataProviderError("clock is outside the supported datetime range") from exc
        if cutoff_ms <= 0:
            raise DataProviderError("clock must be after the Unix epoch")
        query = urlencode(
            {
                "symbol": self.config.symbol,
                "interval": self.config.timeframe,
                "limit": min(self.config.history_limit + 1, 1000),
                "endTime": cutoff_ms - 1,
                "timeZone": "0",
            }
        )
        try:
            payload = self._transport(f"{KLINES_ENDPOINT}?{query}", self.config.timeout_seconds)
        except HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            error_type = RateLimitError if exc.code in (418, 429) else ProviderHTTPError
            exc.close()
            raise error_type(exc.code, retry_after) from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise DataProviderError(f"Binance public transport failed: {exc}") from exc
        if isinstance(payload, dict):
            raise DataProviderError(
                f"Binance returned an error object (code {payload.get('code', 'unknown')})"
            )
        if not isinstance(payload, list):
            raise DataValidationError("Binance klines response must be a list")
        rows = []
        incomplete = 0
        for index, row in enumerate(payload, start=1):
            if not isinstance(row, list) or len(row) != 12:
                raise DataValidationError(f"Binance row {index}: expected a 12-field kline")
            opening, closing = row[0], row[6]
            if (
                type(opening) is not int
                or type(closing) is not int
                or opening < 0
                or closing < opening
            ):
                raise DataValidationError(f"Binance row {index}: invalid open/close time metadata")
            parse_timestamp(opening)
            parse_timestamp(closing)
            if closing >= cutoff_ms:
                incomplete += 1
                continue
            rows.append(dict(zip(OHLCV_COLUMNS, row[:6], strict=True)))
        batch = normalize_ohlcv(
            rows,
            missing_value_policy=self.config.missing_value_policy,
            history_limit=self.config.history_limit,
        )
        return replace(
            batch,
            report=replace(
                batch.report, input_rows=len(payload), incomplete_rows_dropped=incomplete
            ),
        )
