"""Phase 35C: additive Retry-After parser.

Consumes existing ProviderHTTPError.retry_after contract (str | None) and
exposes parsed delay in seconds.

Supports:
- Delta-seconds: "10" → 10.0
- HTTP-date: "Wed, 21 Oct 2015 07:28:00 GMT" → delta vs now

Safe handling:
- Valid positive → float seconds
- Zero → 0.0 (caller decides safe contract; effective delay will be max(backoff, retry_after))
- Negative, non-finite, malformed → None (treated as absent)
- Excessively large → capped to MAX_RETRY_AFTER_SECONDS (explicit, deterministic)
- Timezone-aware HTTP-date handling via email.utils.parsedate_to_datetime
- No exception escapes for malformed headers
- Deterministic under injected now
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

MAX_RETRY_AFTER_SECONDS: Final[float] = 3600.0  # 1 hour explicit cap
MIN_RETRY_AFTER_SECONDS: Final[float] = 0.0

from smcsignal.live.market_feed import LiveFeedError  # noqa: E402


class RateLimitedFeedError(LiveFeedError):
    """Rate-limit failure preserving Retry-After.

    Subclasses LiveFeedError so Phase 35A poll loop treats it as recoverable
    without modifying frozen RECOVERABLE_ERRORS tuple.
    """

    def __init__(
        self,
        status_code: int = 0,
        retry_after: str | None = None,
        retry_after_seconds: float | None = None,
        message: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        self.retry_after_seconds = retry_after_seconds
        base_msg = message or f"rate limited (HTTP {status_code})"
        if retry_after is not None:
            base_msg += f", Retry-After: {retry_after}"
        if retry_after_seconds is not None:
            base_msg += f" ({retry_after_seconds}s)"
        super().__init__(base_msg)


def _is_finite_number(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse Retry-After header value into delay seconds.

    Returns None if value is absent, malformed, negative, or non-finite.
    Returns float in [0, MAX_RETRY_AFTER_SECONDS] otherwise (capped).
    Zero is returned as 0.0 (valid per RFC, caller may apply max(backoff,0)).

    now: timezone-aware datetime used for HTTP-date calculation; defaults to UTC now.
    """

    if value is None:
        return None

    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    # Try delta-seconds first (integer seconds per RFC, but allow float string)
    try:
        # Reject non-numeric that looks like date containing commas/letters
        # Attempt to parse as float
        seconds = float(stripped)
        if not math.isfinite(seconds):
            return None
        if seconds < 0:
            return None
        # Cap
        capped = min(seconds, MAX_RETRY_AFTER_SECONDS)
        # Also ensure >= MIN
        if capped < MIN_RETRY_AFTER_SECONDS:
            return None
        return float(capped)
    except ValueError:
        # Not a number, try HTTP-date
        pass

    # Try HTTP-date
    try:
        dt = parsedate_to_datetime(stripped)
        if dt is None:
            return None
        # Ensure timezone-aware
        if dt.tzinfo is None:
            # Per RFC, HTTP-date should be GMT, treat as UTC if naive
            dt = dt.replace(tzinfo=UTC)
        # now handling
        if now is None:
            now_dt = datetime.now(UTC)
        else:
            if not isinstance(now, datetime) or now.tzinfo is None:
                # Invalid now injection → treat as absent to avoid exception
                return None
            now_dt = now.astimezone(UTC)

        delta = (dt.astimezone(UTC) - now_dt).total_seconds()
        if not math.isfinite(delta):
            return None
        if delta < 0:
            # Date in past → treat as 0 (immediate retry allowed, but backoff will dominate)
            return 0.0
        capped = min(delta, MAX_RETRY_AFTER_SECONDS)
        return float(capped)
    except (ValueError, TypeError, OverflowError):
        return None
