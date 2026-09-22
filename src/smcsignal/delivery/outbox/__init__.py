"""Phase 35D: the durable delivery outbox (additive over the frozen stack).

Public surface of the outbox core: the lifecycle states and record schema
(``models``), the atomic file store (``store``), the state-machine sink that
wraps any injected ``PayloadSink`` (``sink``), and deterministic startup
reconciliation (``reconcile``). The live wiring that composes these with the
frozen live service lives in ``smcsignal.live.outbox_wiring`` — nothing in
this package imports the live boundary.
"""

from smcsignal.delivery.outbox.models import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_PER_DRAIN,
    MAX_MAX_ATTEMPTS,
    META_SCHEMA,
    MIN_MAX_ATTEMPTS,
    RECORD_SCHEMA,
    RETRYABLE_FAILURE_CATEGORIES,
    TERMINAL_STATES,
    AttemptEntry,
    OutboxConfig,
    OutboxPayload,
    OutboxRecord,
    OutboxState,
    new_queued_record,
    parse_meta_document,
    receipt_allows_retry,
)
from smcsignal.delivery.outbox.reconcile import (
    ReconciliationReport,
    expected_delivery_id,
    reconcile_missing,
)
from smcsignal.delivery.outbox.sink import OutboxPayloadSink
from smcsignal.delivery.outbox.store import FileOutboxStore, OutboxStoreError, key_digest

__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_PER_DRAIN",
    "MAX_MAX_ATTEMPTS",
    "META_SCHEMA",
    "MIN_MAX_ATTEMPTS",
    "RECORD_SCHEMA",
    "RETRYABLE_FAILURE_CATEGORIES",
    "TERMINAL_STATES",
    "AttemptEntry",
    "FileOutboxStore",
    "OutboxConfig",
    "OutboxPayload",
    "OutboxPayloadSink",
    "OutboxRecord",
    "OutboxState",
    "OutboxStoreError",
    "ReconciliationReport",
    "expected_delivery_id",
    "key_digest",
    "new_queued_record",
    "parse_meta_document",
    "receipt_allows_retry",
    "reconcile_missing",
]
