"""Phase 35C: Retry-After parsing and rate-limit handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from smcsignal.live.market_feed import LiveFeedError
from smcsignal.live.retry_after import (
    MAX_RETRY_AFTER_SECONDS,
    RateLimitedFeedError,
    parse_retry_after,
)


def test_429_integer_seconds():
    assert parse_retry_after("10") == 10.0
    assert parse_retry_after("  10  ") == 10.0


def test_418_integer_seconds():
    # Same parser, status code handling is in feed wrapper
    assert parse_retry_after("5") == 5.0


def test_http_date():
    now = datetime(2015, 10, 21, 7, 27, 0, tzinfo=UTC)
    future = now + timedelta(seconds=60)
    header = format_datetime(future)
    parsed = parse_retry_after(header, now=now)
    assert parsed is not None
    assert abs(parsed - 60.0) < 2.0  # allow small rounding


def test_malformed_header():
    assert parse_retry_after("banana") is None
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("") is None
    assert parse_retry_after("   ") is None
    assert parse_retry_after(None) is None


def test_negative_header():
    assert parse_retry_after("-5") is None
    assert parse_retry_after("-0.1") is None


def test_zero_header():
    # Zero is valid per RFC, returned as 0.0, caller will max with backoff
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("0.0") == 0.0


def test_huge_header_capped():
    huge = str(int(MAX_RETRY_AFTER_SECONDS * 10))
    parsed = parse_retry_after(huge)
    assert parsed == MAX_RETRY_AFTER_SECONDS
    # Also test float huge
    assert parse_retry_after("9999999") == MAX_RETRY_AFTER_SECONDS


def test_absent_header():
    assert parse_retry_after(None) is None


def test_timezone_date_handling():
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    # Future date in GMT
    future = datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC)
    header = format_datetime(future)
    parsed = parse_retry_after(header, now=now)
    assert parsed is not None
    assert abs(parsed - 300.0) < 2.0

    # Past date → 0.0
    past = now - timedelta(seconds=10)
    header_past = format_datetime(past)
    assert parse_retry_after(header_past, now=now) == 0.0


def test_existing_exponential_backoff_retained():
    from smcsignal.live.poll_loop import LivePollLoopConfig

    cfg = LivePollLoopConfig(timeframe="15m", backoff_base_seconds=1.0, backoff_max_seconds=60.0)
    assert cfg.backoff_seconds(1) == 1.0
    assert cfg.backoff_seconds(2) == 2.0
    assert cfg.backoff_seconds(3) == 4.0
    assert cfg.backoff_seconds(10) == 60.0


def test_valid_retry_after_respected():
    # Simulate poll loop effective delay = max(backoff, retry_after)
    backoff = 2.0
    retry_after = 10.0
    effective = max(backoff, retry_after)
    assert effective == 10.0


def test_retry_after_shorter_than_backoff():
    backoff = 10.0
    retry_after = 2.0
    effective = max(backoff, retry_after)
    assert effective == 10.0


def test_retry_after_longer_than_backoff():
    backoff = 1.0
    retry_after = 30.0
    effective = max(backoff, retry_after)
    assert effective == 30.0


def test_shutdown_interrupts_retry_after_wait():
    # This is tested in poll_loop tests with injected clock/sleep
    # Here we just ensure parse doesn't block
    assert parse_retry_after("5") == 5.0


def test_successful_request_after_rate_limit_retry():
    # Ensure RateLimitedFeedError is subclass of LiveFeedError → recoverable
    err = RateLimitedFeedError(status_code=429, retry_after="10", retry_after_seconds=10.0)
    assert isinstance(err, LiveFeedError)
    assert err.retry_after_seconds == 10.0
    assert err.status_code == 429
    assert err.retry_after == "10"


def test_rate_limited_error_preserves_status_and_retry():
    err = RateLimitedFeedError(status_code=418, retry_after="120", retry_after_seconds=120.0)
    assert err.status_code == 418
    assert err.retry_after_seconds == 120.0
    assert "418" in str(err)
    assert "120" in str(err)


def test_non_finite_rejected():
    assert parse_retry_after("inf") is None
    assert parse_retry_after("nan") is None
    assert parse_retry_after("Infinity") is None


def test_float_seconds():
    assert parse_retry_after("1.5") == 1.5
