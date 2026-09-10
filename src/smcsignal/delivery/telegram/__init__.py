"""Phase 24D-C Telegram transport package.

This package implements the real Telegram Bot API transport behind the Phase
24D-B ``PayloadSink``/``TransportPayload`` foundation. It is strictly
downstream of signal generation: it only sends already-rendered message text and
an optional already-prepared chart document to a configured Telegram chat. It is
never imported by the frozen Phase 24B presentation core or the Phase 24C
orchestrator (``smcsignal.delivery`` does not import this package).

Telegram remains output-only. Nothing here can influence signal generation,
halal classification, eligibility, Phase 23 governance, or strategy, and there is
no trading, execution, broker, exchange, portfolio, or order functionality.
No token or chat id is ever committed; a missing token disables the transport.
"""

from smcsignal.delivery.telegram.audit import (
    TelegramAuditRecord,
    redact_chat_id,
    telegram_audit_record,
)
from smcsignal.delivery.telegram.config import TelegramConfig, load_telegram_config
from smcsignal.delivery.telegram.destination import (
    resolve_chat_id,
    validate_chat_id,
)
from smcsignal.delivery.telegram.http import (
    HttpResponse,
    TelegramApiError,
    TelegramHttpClient,
    TelegramTimeoutError,
)
from smcsignal.delivery.telegram.integration import (
    TelegramDeliveryCounters,
    TelegramDeliveryIntegration,
    TelegramDeliveryResult,
    TelegramPayloadBridge,
)
from smcsignal.delivery.telegram.rate_limit import TelegramRateLimiter
from smcsignal.delivery.telegram.sink import TelegramSink

__all__ = [
    "HttpResponse",
    "TelegramApiError",
    "TelegramAuditRecord",
    "TelegramConfig",
    "TelegramDeliveryCounters",
    "TelegramDeliveryIntegration",
    "TelegramDeliveryResult",
    "TelegramHttpClient",
    "TelegramPayloadBridge",
    "TelegramRateLimiter",
    "TelegramSink",
    "TelegramTimeoutError",
    "load_telegram_config",
    "redact_chat_id",
    "resolve_chat_id",
    "telegram_audit_record",
    "validate_chat_id",
]
