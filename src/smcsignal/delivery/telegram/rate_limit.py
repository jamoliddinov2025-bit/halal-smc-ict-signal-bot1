"""Deterministic in-process token bucket for outbound Telegram rate limiting.

The bucket caps how many outbound requests may be issued per second so the bot
stays under Telegram API limits and does not hammer on failures. The clock is
injectable so tests can drive refill deterministically without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from smcsignal.analysis.errors import AnalysisInputError


@dataclass
class TelegramRateLimiter:
    """A token bucket rate limiter with an injectable clock."""

    requests_per_second: float
    capacity: int
    now: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if type(self.requests_per_second) not in (int, float) or self.requests_per_second <= 0:
            raise AnalysisInputError("requests_per_second must be positive")
        if type(self.capacity) is not int or self.capacity < 1:
            raise AnalysisInputError("capacity must be a positive integer")
        self._tokens: float = float(self.capacity)
        self._last: float = self.now()

    def try_acquire(self) -> bool:
        """Attempt to take one token; returns True when permitted.

        Tokens refill continuously up to ``capacity``. When no token is
        available the call returns False (the caller should treat that as a
        rate-limit failure and not send).
        """
        current = self.now()
        elapsed = max(0.0, current - self._last)
        self._last = current
        refill = elapsed * self.requests_per_second
        self._tokens = min(float(self.capacity), self._tokens + refill)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def reset(self) -> None:
        """Refill the bucket to full capacity (used between tests/attempts)."""
        self._tokens = float(self.capacity)
        self._last = self.now()
