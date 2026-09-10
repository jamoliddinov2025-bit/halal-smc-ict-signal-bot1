"""Telegram chat/channel destination handling (strict, redactable).

A logical ``destination_id`` (a stable configured key, as used by the frozen
coordinator) is resolved to a real Telegram chat/channel id through a strict
destination map. The raw chat id is treated as sensitive: it is only used inside
the outbound HTTP call and is redacted everywhere else (audit/log). Invalid or
missing destinations are rejected *before* any network call.
"""

from __future__ import annotations

from collections.abc import Mapping

from smcsignal.analysis.errors import AnalysisInputError

from .audit import redact_chat_id


def validate_chat_id(chat_id: str) -> str:
    """Validate and return a canonical Telegram chat/channel id.

    Accepts a numeric id (``\"-1001234567890\"``) or an ``@username`` channel
    handle. Returns the value unchanged when valid; otherwise raises
    ``AnalysisInputError``.
    """
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise AnalysisInputError("chat_id must be a nonempty string")
    value = chat_id.strip()
    if value.startswith("@"):
        if len(value) < 2:
            raise AnalysisInputError("channel handle must be nonempty after '@'")
        return value
    if value.lstrip("-").isdigit():
        return value
    raise AnalysisInputError("chat_id must be a numeric id or an @channel handle")


def resolve_chat_id(destination_id: str, destinations: Mapping[str, str]) -> str:
    """Resolve a logical ``destination_id`` to a valid Telegram chat id.

    The destination map maps logical keys (the same ``destination_id`` used by
    the frozen coordinator) to raw chat/channel ids. An unknown key or an
    invalid chat id raises ``AnalysisInputError`` so delivery fails before any
    network call.
    """
    if not isinstance(destination_id, str) or not destination_id.strip():
        raise AnalysisInputError("destination_id must be a nonempty string")
    chat_id = destinations.get(destination_id)
    if chat_id is None:
        raise AnalysisInputError(f"unknown destination: {destination_id}")
    return validate_chat_id(chat_id)


def destination_redacted(destination_id: str) -> str:
    """Redact a logical destination id for audit/log output."""
    if not isinstance(destination_id, str) or not destination_id.strip():
        raise AnalysisInputError("destination_id must be a nonempty string")
    return redact_chat_id(destination_id)
