"""Telegram rate limiter (token bucket) tests with an injectable clock."""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.telegram.rate_limit import TelegramRateLimiter


def _clock(ticks: list[float]):
    index = 0

    def now() -> float:
        nonlocal index
        value = ticks[min(index, len(ticks) - 1)]
        index += 1
        return value

    return now


def test_bucket_allows_up_to_capacity() -> None:
    limiter = TelegramRateLimiter(requests_per_second=1.0, capacity=2, now=lambda: 0.0)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False  # empty at same instant


def test_bucket_refills_over_time() -> None:
    # __post_init__ and each try_acquire consume one clock reading.
    now = _clock([0.0, 0.0, 0.0, 2.0, 2.0])
    limiter = TelegramRateLimiter(requests_per_second=1.0, capacity=1, now=now)
    assert limiter.try_acquire() is True  # consume token (t=0)
    assert limiter.try_acquire() is False  # t=0 no refill yet
    assert limiter.try_acquire() is True  # at t=2 one token refilled (capped)


def test_reset_restores_capacity() -> None:
    limiter = TelegramRateLimiter(requests_per_second=1.0, capacity=1, now=lambda: 0.0)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False
    limiter.reset()
    assert limiter.try_acquire() is True


def test_invalid_construction() -> None:
    with pytest.raises(AnalysisInputError):
        TelegramRateLimiter(requests_per_second=0.0, capacity=1)
    with pytest.raises(AnalysisInputError):
        TelegramRateLimiter(requests_per_second=1.0, capacity=0)
