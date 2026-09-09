"""Deterministic, transport-safe text formatting for Phase 24 presentation.

Formatting is a pure function of its inputs. It never depends on the current
time, randomness, locale, or environment, never changes the underlying signal,
and is safe to embed in HTML output because every dynamic value is escaped.
"""

from __future__ import annotations

import html
import re
from decimal import Decimal

_TRAILING_ZEROS = re.compile(r"0+$")


def escape_html(value: object) -> str:
    """HTML-escape an arbitrary dynamic value so it cannot break markup."""
    return html.escape(str(value), quote=True)


def format_decimal(value: Decimal, *, max_decimals: int = 8) -> str:
    """Deterministic plain-text rendering of a finite Decimal.

    The value is never coerced to float. Only the *display* is bounded to
    ``max_decimals`` digits after the point; trailing zeros are trimmed so the
    output is stable regardless of the internal scale.
    """
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("format_decimal requires a finite Decimal")
    if type(max_decimals) is not int or max_decimals < 0:
        raise ValueError("max_decimals must be a nonnegative integer")
    text = format(value, "f")
    if "." not in text:
        return text
    integer, _, fraction = text.partition(".")
    if len(fraction) > max_decimals:
        fraction = fraction[:max_decimals]
    trimmed = _TRAILING_ZEROS.sub("", fraction)
    if not trimmed:
        return integer
    return f"{integer}.{trimmed}"


def format_timestamp(value: object) -> str:
    """Deterministic ISO timestamp (seconds) for audit/presentation only."""
    from datetime import datetime

    if not isinstance(value, datetime):
        raise ValueError("format_timestamp requires a datetime")
    return value.isoformat(timespec="seconds")


def split_caption(text: str, limit: int) -> tuple[str, ...]:
    """Split one caption into deterministic parts of at most ``limit`` chars.

    Splitting happens only at line boundaries so that no factual line is torn
    in half. If a single line still exceeds the limit it is force-broken at the
    limit (also deterministic). Returns nonempty parts; never re-orders text.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if text == "":
        return ("",)
    if len(text) <= limit:
        return (text,)
    parts: list[str] = []
    buffer = ""
    for line in text.split("\n"):
        candidate = line if buffer == "" else buffer + "\n" + line
        if len(candidate) <= limit:
            buffer = candidate
            continue
        # flush the current buffer before handling an oversized line
        if buffer != "":
            parts.append(buffer)
            buffer = ""
        remaining = line
        while len(remaining) > limit:
            parts.append(remaining[:limit])
            remaining = remaining[limit:]
        buffer = remaining
    if buffer != "":
        parts.append(buffer)
    return tuple(parts)
