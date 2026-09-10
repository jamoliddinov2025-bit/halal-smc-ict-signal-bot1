"""Telegram transport audit glue (deterministic, secret-free).

The Telegram transport must never expose a bot token or a raw chat id in any
audit/log/error output. This module provides a redaction helper for chat ids and
a deterministic, machine-readable audit record built from a frozen
``TransportPayload`` and ``DeliveryReceipt``. Tokens are never represented here
at all: a token that reaches this module is a caller bug, so the helper returns a
fixed marker rather than any portion of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import digest

from ..audit import redact

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..models import DeliveryReceipt
    from ..transport import TransportPayload

_REDACTED_TOKEN = "<token-redacted>"
_AUDIT_METHOD = "telegram-delivery-audit-v1"


def redact_chat_id(chat_id: str, *, keep: int = 4) -> str:
    """Deterministically redact a chat/channel id, keeping only its tail."""
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise AnalysisInputError("chat_id must be a nonempty string")
    return redact(chat_id, keep=keep)


def redact_token(token: object) -> str:
    """Return a fixed marker for any token value (never leaks it)."""
    if token is None:
        raise AnalysisInputError("token is None")
    return _REDACTED_TOKEN


@dataclass(frozen=True, slots=True)
class TelegramAuditRecord:
    """Deterministic, secret-free audit record for one Telegram delivery.

    Carries the frozen delivery identity, a redacted chat id, the frozen
    state/category, and a content digest. No timestamps (they would break
    determinism) and no secret material.
    """

    delivery_id: str
    message_id: str
    signal_id: str
    chat_redacted: str
    state: str
    failure_category: str
    attempt_number: int
    chart_artifact_id: str | None
    message_digest: str


def telegram_audit_record(
    payload: TransportPayload,
    receipt: DeliveryReceipt,
    *,
    chat_id: str | None = None,
) -> TelegramAuditRecord:
    """Build a deterministic audit record from a payload and its receipt.

    ``chat_id`` is the resolved chat id used for the send; it is stored only in
    redacted form. If omitted, the payload's logical destination id is redacted
    instead. The record never contains the token.
    """
    from ..models import DeliveryReceipt
    from ..transport import TransportPayload

    if not isinstance(payload, TransportPayload):
        raise AnalysisInputError("telegram_audit_record requires a TransportPayload")
    if not isinstance(receipt, DeliveryReceipt):
        raise AnalysisInputError("telegram_audit_record requires a DeliveryReceipt")
    source = chat_id if (chat_id is not None and chat_id.strip()) else payload.destination_id
    digest_value = digest(
        {
            "methodology": _AUDIT_METHOD,
            "kind": "telegram-caption",
            "message_id": payload.message_id,
            "caption": payload.caption,
        }
    )
    chart = payload.chart
    chart_artifact_id = chart.artifact_id if chart is not None else None
    return TelegramAuditRecord(
        delivery_id=payload.delivery_id,
        message_id=payload.message_id,
        signal_id=payload.signal_id,
        chat_redacted=redact_chat_id(source),
        state=receipt.state.value,
        failure_category=receipt.failure_category.value,
        attempt_number=receipt.attempt_number,
        chart_artifact_id=chart_artifact_id,
        message_digest=digest_value,
    )
