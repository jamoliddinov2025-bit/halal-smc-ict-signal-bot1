"""Offline Binance contract tests: mapping, cutoff, HTTP errors, and limits."""

import io
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from smcsignal.data import (
    BinancePublicDataProvider,
    DataProvider,
    DataProviderError,
    DataValidationError,
    MarketDataConfig,
    ProviderHTTPError,
    RateLimitError,
    binance,
)

OPEN_MS = 1704067200000
NOW = datetime(2024, 1, 1, 0, 31, tzinfo=UTC)


def kline(opening=OPEN_MS, **overrides):
    row = [
        opening,
        "100.00",
        "110.00",
        "90.00",
        "105.00",
        "10.50",
        opening + 899999,
        "1050.00",
        7,
        "5",
        "500",
        "0",
    ]
    for key, value in overrides.items():
        row[int(key)] = value
    return row


def config(**overrides):
    return MarketDataConfig(
        **{
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "data_source": "binance_public",
            "history_limit": 2,
            **overrides,
        }
    )


def source(payload, **overrides):
    return BinancePublicDataProvider(
        config(**overrides), transport=lambda url, timeout: payload, clock=lambda: NOW
    )


def test_public_request_is_correct_and_closed_rows_are_mapped() -> None:
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        return [kline(), kline(OPEN_MS + 900000), kline(OPEN_MS + 1800000)]

    provider = BinancePublicDataProvider(config(), transport=transport, clock=lambda: NOW)
    assert isinstance(provider, DataProvider)
    assert calls == []
    batch = provider.fetch_ohlcv()
    url, timeout = calls[0]
    assert len(calls) == 1
    assert urlparse(url).scheme == "https"
    assert urlparse(url).netloc == "data-api.binance.vision"
    assert urlparse(url).path == "/api/v3/klines"
    assert parse_qs(urlparse(url).query) == {
        "symbol": ["BTCUSDT"],
        "interval": ["15m"],
        "limit": ["3"],
        "endTime": [str(OPEN_MS + 1860000 - 1)],
        "timeZone": ["0"],
    }
    assert timeout == 10.0
    assert len(batch.candles) == 2
    assert batch.candles[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert batch.candles[0].open == Decimal("100.00")
    assert batch.candles[0].volume == Decimal("10.50")
    assert batch.report.input_rows == 3
    assert batch.report.incomplete_rows_dropped == 1


def test_candle_is_not_closed_at_its_final_millisecond() -> None:
    closing = datetime(2024, 1, 1, 0, 14, 59, 999000, tzinfo=UTC)
    at_close = BinancePublicDataProvider(
        config(), transport=lambda u, t: [kline()], clock=lambda: closing
    )
    after_close = BinancePublicDataProvider(
        config(),
        transport=lambda u, t: [kline()],
        clock=lambda: datetime(2024, 1, 1, 0, 15, tzinfo=UTC),
    )
    assert at_close.fetch_ohlcv().candles == ()
    assert len(after_close.fetch_ohlcv().candles) == 1


def test_future_candles_are_excluded() -> None:
    batch = source([kline(OPEN_MS + 86400000)]).fetch_ohlcv()
    assert batch.candles == ()
    assert batch.report.incomplete_rows_dropped == 1


def test_one_page_limit_is_capped_at_1000() -> None:
    calls = []

    def transport(url, timeout):
        calls.append(parse_qs(urlparse(url).query))
        return []

    provider = BinancePublicDataProvider(
        config(history_limit=1000), transport=transport, clock=lambda: NOW
    )
    assert provider.fetch_ohlcv().candles == ()
    assert calls[0]["limit"] == ["1000"]
    assert len(calls) == 1


def test_order_duplicates_and_window_use_shared_validator() -> None:
    batch = source([kline(OPEN_MS + 900000), kline(), kline()], history_limit=1).fetch_ohlcv()
    assert len(batch.candles) == 1
    assert batch.candles[0].timestamp.minute == 15
    assert batch.report.duplicates_removed == 1
    assert batch.report.rows_trimmed == 1
    assert batch.report.reordered is True


def test_conflicting_binance_duplicates_are_rejected() -> None:
    with pytest.raises(DataValidationError, match="conflicting duplicate"):
        source([kline(), kline(**{"4": "106"})]).fetch_ohlcv()


def test_missing_rows_and_incomplete_rows_have_separate_audit_counts() -> None:
    batch = source(
        [kline(**{"5": ""}), kline(OPEN_MS + 900000), kline(OPEN_MS + 1800000)],
        missing_value_policy="drop",
    ).fetch_ohlcv()
    report = asdict(batch.report)
    assert report["input_rows"] == 3
    assert (
        report["output_rows"]
        == report["missing_rows_dropped"]
        == report["incomplete_rows_dropped"]
        == 1
    )
    assert report["input_rows"] == sum(
        report[key]
        for key in [
            "output_rows",
            "duplicates_removed",
            "missing_rows_dropped",
            "rows_trimmed",
            "incomplete_rows_dropped",
        ]
    )


@pytest.mark.parametrize("payload", [None, "[]", 42, True, [None], [[]], [[0] * 11], [[0] * 13]])
def test_invalid_response_shapes_are_rejected(payload) -> None:
    with pytest.raises(DataValidationError):
        source(payload).fetch_ohlcv()


@pytest.mark.parametrize(
    "payload", [{"code": -1121, "msg": "Invalid symbol."}, {"unexpected": "object"}]
)
def test_binance_error_objects_never_become_empty_success(payload) -> None:
    with pytest.raises(DataProviderError, match="error object"):
        source(payload).fetch_ohlcv()


@pytest.mark.parametrize(
    ("index", "value"),
    [
        ("0", None),
        ("0", True),
        ("0", "1704067200000"),
        ("0", -1),
        ("6", None),
        ("6", 0),
        ("6", True),
        ("6", 10**30),
    ],
)
def test_invalid_timestamp_metadata_is_rejected(index, value) -> None:
    with pytest.raises(DataValidationError):
        source([kline(**{index: value})]).fetch_ohlcv()


def test_invalid_ohlcv_is_not_silently_returned() -> None:
    with pytest.raises(DataValidationError, match="high"):
        source([kline(**{"2": "1"})]).fetch_ohlcv()


@pytest.mark.parametrize("status", [418, 429, 400, 403, 451, 500, 503])
def test_http_errors_preserve_status_and_do_not_retry(status) -> None:
    calls = []
    headers = Message()
    headers["Retry-After"] = "120"

    def transport(url, timeout):
        calls.append(url)
        raise HTTPError(url, status, "upstream failure", headers, None)

    provider = BinancePublicDataProvider(config(), transport=transport, clock=lambda: NOW)
    expected = RateLimitError if status in (418, 429) else ProviderHTTPError
    with pytest.raises(expected) as exc:
        provider.fetch_ohlcv()
    assert exc.value.status_code == status
    assert exc.value.retry_after == "120"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error", [TimeoutError("timeout"), URLError("DNS failure"), OSError("socket failure")]
)
def test_transport_failures_are_actionable_without_fallback(error) -> None:
    def transport(url, timeout):
        raise error

    provider = BinancePublicDataProvider(config(), transport=transport, clock=lambda: NOW)
    with pytest.raises(DataProviderError, match="transport failed"):
        provider.fetch_ohlcv()


@pytest.mark.parametrize(
    "now", [datetime(2024, 1, 1), "2024-01-01T00:00:00Z", datetime(1970, 1, 1, tzinfo=UTC)]
)
def test_invalid_clock_fails_before_transport(now) -> None:
    def never_called(url, timeout):
        pytest.fail("transport should not be called")

    provider = BinancePublicDataProvider(config(), transport=never_called, clock=lambda: now)
    with pytest.raises(DataProviderError, match="clock"):
        provider.fetch_ohlcv()


def test_real_transport_boundary_uses_get_and_no_auth_headers(monkeypatch) -> None:
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps([kline()]).encode())

    monkeypatch.setattr(binance, "urlopen", fake_urlopen)
    batch = BinancePublicDataProvider(config(), clock=lambda: NOW).fetch_ohlcv()
    assert len(batch.candles) == 1
    request, timeout = calls[0]
    assert request.method == "GET"
    assert request.data is None
    assert {name.lower() for name in request.headers} == {"accept", "user-agent"}
    assert "signature" not in request.full_url
    assert timeout == 10.0


@pytest.mark.parametrize(
    "body", [b"not-json", b"<html>blocked</html>", b"\xff", b"[NaN]", b"[Infinity]"]
)
def test_malformed_json_is_actionable(monkeypatch, body) -> None:
    monkeypatch.setattr(binance, "urlopen", lambda request, timeout: io.BytesIO(body))
    with pytest.raises(DataProviderError, match="UTF-8 JSON"):
        BinancePublicDataProvider(config(), clock=lambda: NOW).fetch_ohlcv()


def test_oversized_response_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(binance, "MAX_RESPONSE_BYTES", 16)
    monkeypatch.setattr(binance, "urlopen", lambda request, timeout: io.BytesIO(b" " * 17))
    with pytest.raises(DataProviderError, match="safety limit"):
        BinancePublicDataProvider(config(), clock=lambda: NOW).fetch_ohlcv()


def test_default_clock_microseconds_do_not_break_fetch(monkeypatch) -> None:
    monkeypatch.setattr(binance, "_get_json", lambda url, timeout: [])
    assert BinancePublicDataProvider(config()).fetch_ohlcv().candles == ()


def test_pre_epoch_clock_is_a_provider_error() -> None:
    provider = BinancePublicDataProvider(config(), clock=lambda: datetime(1969, 1, 1, tzinfo=UTC))
    with pytest.raises(DataProviderError, match="clock"):
        provider.fetch_ohlcv()


def test_deeply_nested_json_is_an_actionable_response_error(monkeypatch) -> None:
    body = b"[" * 2000 + b"]" * 2000
    monkeypatch.setattr(binance, "urlopen", lambda request, timeout: io.BytesIO(body))
    with pytest.raises(DataProviderError, match="UTF-8 JSON"):
        BinancePublicDataProvider(config(), clock=lambda: NOW).fetch_ohlcv()


def test_incomplete_http_response_is_a_provider_error() -> None:
    from http.client import IncompleteRead

    def transport(url, timeout):
        raise IncompleteRead(b"partial data")

    with pytest.raises(DataProviderError, match="transport failed"):
        BinancePublicDataProvider(config(), transport=transport, clock=lambda: NOW).fetch_ohlcv()
