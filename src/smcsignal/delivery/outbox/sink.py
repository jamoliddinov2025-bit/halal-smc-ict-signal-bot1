"""Phase 35D: the durable outbox payload sink (state machine owner).

``OutboxPayloadSink`` implements the frozen ``PayloadSink`` boundary around an
injected inner transport sink. It owns the locked durable state machine and
nothing else: it persists intent before any transport call, persists the
settled outcome after the receipt, and never renders, never re-implements
transport behavior, and never schedules. The inner sink is any ``PayloadSink``
—the real frozen transport in production, an offline double in tests—so this
module contains no transport specifics at all.

Locked ordering for one inline attempt::

    persist QUEUED (first sight of this delivery id)
      → persist IN_FLIGHT (attempt counted, before the transport call)
        → delegate to the inner sink
          → persist terminal / WAITING
            → return the frozen receipt unchanged

Durability guarantee: a crash at any point leaves a durable record from which
restart recovery (``IN_FLIGHT`` ⇒ ambiguous attempt already consumed) and the
bounded drain can continue without losing the intent and without ever
reporting a success that the transport did not confirm.

Duplicate-risk representation: any attempt ending in the frozen ``UNKNOWN``
state — or recovered as ``IN_FLIGHT`` after a restart, whose outcome is
unknowable — sets ``ambiguous_attempt_seen``; a later terminal success then
carries ``possibly_duplicated``. No exactly-once claim is made anywhere.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.models import (
    DeliveryReceipt,
    DeliveryState,
    FailureCategory,
)
from smcsignal.delivery.transport import PayloadSink, TransportPayload

from .models import (
    TERMINAL_STATES,
    AttemptEntry,
    OutboxConfig,
    OutboxRecord,
    OutboxState,
    ensure_utc,
    new_queued_record,
    receipt_allows_retry,
)
from .store import FileOutboxStore


def _default_clock() -> datetime:
    return datetime.now(UTC)


class OutboxPayloadSink(PayloadSink):
    """Durable wrapper over one injected inner ``PayloadSink``.

    Construction performs the locked restart recovery (T9): every persisted
    ``IN_FLIGHT`` record is settled as an ambiguous attempt — ``WAITING`` when
    budget remains, ``AMBIGUOUS`` when it does not. Draining is bounded: at
    most ``config.max_per_drain`` records per pass, one attempt per record per
    pass, cooldown-gated, deterministic order. Draining never runs on its own
    clock; the caller (the frozen cycle machinery) invokes it.
    """

    def __init__(
        self,
        store: FileOutboxStore,
        inner: PayloadSink,
        *,
        config: OutboxConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, FileOutboxStore):
            raise AnalysisInputError("OutboxPayloadSink requires a FileOutboxStore")
        if inner is None or not callable(getattr(inner, "deliver_payload", None)):
            raise AnalysisInputError("OutboxPayloadSink requires an inner payload sink")
        self._store = store
        self._inner = inner
        self._config = config if config is not None else OutboxConfig()
        self._clock = clock if clock is not None else _default_clock
        self._recover_in_flight()

    # -- operator visibility ------------------------------------------------------

    @property
    def store(self) -> FileOutboxStore:
        return self._store

    @property
    def inner(self) -> PayloadSink:
        return self._inner

    @property
    def config(self) -> OutboxConfig:
        return self._config

    def summary(self) -> Mapping[str, int]:
        """Deterministic per-state tally plus quarantine/meta health."""
        counts: dict[str, int] = {state.value: 0 for state in OutboxState}
        for record in self._store.all_records():
            counts[record.state.value] += 1
        counts["quarantined"] = self._store.quarantine_count
        counts["meta_untrusted"] = 1 if self._store.meta_failure is not None else 0
        return counts

    # -- the frozen PayloadSink boundary -------------------------------------------

    def deliver_payload(self, payload: TransportPayload) -> DeliveryReceipt:
        """Persist intent, attempt through the inner sink, persist the outcome.

        Terminal records are never re-attempted (T10): a repeated call for an
        already-terminal delivery id returns the durable receipt without
        touching the inner sink, so no duplicate can ever be produced through
        this boundary.
        """
        if not isinstance(payload, TransportPayload):
            raise AnalysisInputError("OutboxPayloadSink requires a TransportPayload")
        record = self._store.load_record(payload.delivery_id)
        if record is None:
            record = new_queued_record(
                payload, now=self._clock(), max_attempts=self._config.max_attempts
            )
            self._store.save_record(record)
        if record.state is OutboxState.IN_FLIGHT:
            record = self._recover_record(record)
        if record.terminal:
            return self._receipt_from_record(record)
        return self._attempt(record, payload)

    def drain(self, now: datetime | None = None) -> int:
        """One bounded drain pass over eligible durable records.

        Considers ``QUEUED`` (reconciled intents) and ``WAITING`` records in
        deterministic delivery-id order, skipping records inside their cooldown
        or with no budget left, and attempts at most ``max_per_drain`` of them
        — exactly one attempt each. Returns the number of attempts made.
        """
        moment = ensure_utc(now if now is not None else self._clock())
        attempts_made = 0
        for record in self._store.all_records():
            if attempts_made >= self._config.max_per_drain:
                break
            if record.state is OutboxState.IN_FLIGHT:
                record = self._recover_record(record)
            if record.state not in (OutboxState.QUEUED, OutboxState.WAITING):
                continue
            if not record.budget_remaining:
                self._settle_budget_exhausted(record)
                continue
            if not self._cooldown_elapsed(record, moment):
                continue
            payload = self._payload_from_record(record)
            self._attempt(record, payload)
            attempts_made += 1
        return attempts_made

    # -- state machine core -----------------------------------------------------------

    def _attempt(self, record: OutboxRecord, payload: TransportPayload) -> DeliveryReceipt:
        """T2/T3 → transport call → T4..T8; the receipt is returned unchanged."""
        if not record.budget_remaining:
            record = self._settle_budget_exhausted(record)
            return self._receipt_from_record(record)
        moment = ensure_utc(self._clock())
        record = replace(
            record,
            state=OutboxState.IN_FLIGHT,
            attempts_used=record.attempts_used + 1,
            last_attempt_at=moment,
            updated_at=moment,
        )
        self._store.save_record(record)  # durable BEFORE the transport call
        receipt = self._inner.deliver_payload(payload)
        return self._settle(record, receipt)

    def _settle(self, record: OutboxRecord, receipt: DeliveryReceipt) -> DeliveryReceipt:
        """Persist the post-attempt state (T4-T8) from one frozen receipt."""
        if not isinstance(receipt, DeliveryReceipt):
            raise AnalysisInputError("the inner sink must return a DeliveryReceipt")
        moment = ensure_utc(self._clock())
        entry = AttemptEntry(
            attempt=record.attempts_used,
            receipt_state=receipt.state,
            failure_category=receipt.failure_category,
            at=moment,
        )
        ambiguous = record.ambiguous_attempt_seen
        duplicated = record.possibly_duplicated
        if receipt.state in (DeliveryState.DELIVERED, DeliveryState.SENT):
            state = OutboxState.DELIVERED
            duplicated = ambiguous
        elif receipt.state is DeliveryState.SKIPPED_DUPLICATE:
            # Defensive only: the frozen registry intercepts this state before
            # any sink call, so it cannot reach this wrapper through the live
            # path. Treat it as the success it reports.
            state = OutboxState.DELIVERED
        elif not receipt_allows_retry(receipt.state, receipt.failure_category):
            state = OutboxState.FAILED_TERMINAL
        elif record.attempts_used >= record.max_attempts:
            if receipt.state is DeliveryState.UNKNOWN:
                state = OutboxState.AMBIGUOUS
                duplicated = True
            else:
                state = OutboxState.EXHAUSTED
        else:
            state = OutboxState.WAITING
            ambiguous = ambiguous or receipt.state is DeliveryState.UNKNOWN
        settled = replace(
            record,
            state=state,
            updated_at=moment,
            last_receipt_state=receipt.state,
            last_failure_category=receipt.failure_category,
            ambiguous_attempt_seen=ambiguous,
            possibly_duplicated=duplicated,
            attempt_log=record.attempt_log + (entry,),
        )
        self._store.save_record(settled)
        return receipt

    def _settle_budget_exhausted(self, record: OutboxRecord) -> OutboxRecord:
        """Defensive terminal settlement when budget is gone mid-lifecycle."""
        moment = ensure_utc(self._clock())
        if record.state in TERMINAL_STATES:
            return record
        if record.last_receipt_state is DeliveryState.UNKNOWN:
            state = OutboxState.AMBIGUOUS
            duplicated = True
        else:
            state = OutboxState.EXHAUSTED
            duplicated = record.possibly_duplicated
        settled = replace(
            record,
            state=state,
            updated_at=moment,
            possibly_duplicated=duplicated,
        )
        self._store.save_record(settled)
        return settled

    def _recover_record(self, record: OutboxRecord) -> OutboxRecord:
        """T9: an ``IN_FLIGHT`` record's attempt outcome is unknowable.

        The attempt was already counted when the ``IN_FLIGHT`` row was written
        before the transport call, so recovery only classifies: ``WAITING``
        while budget remains, ``AMBIGUOUS`` once it is consumed. The ambiguity
        flag is always set — a restart here means the transport may or may not
        have accepted the attempt.
        """
        moment = ensure_utc(self._clock())
        if record.attempts_used >= record.max_attempts:
            state = OutboxState.AMBIGUOUS
            duplicated = True
        else:
            state = OutboxState.WAITING
            duplicated = record.possibly_duplicated
        # The interrupted attempt left no receipt; record it as the ambiguous
        # UNKNOWN/TIMEOUT outcome it is, keeping the attempt log complete and
        # sequential (one durable entry per consumed attempt).
        log = record.attempt_log
        if len(log) < record.attempts_used:
            log = log + (
                AttemptEntry(
                    attempt=record.attempts_used,
                    receipt_state=DeliveryState.UNKNOWN,
                    failure_category=FailureCategory.TIMEOUT,
                    at=moment,
                ),
            )
        recovered = replace(
            record,
            state=state,
            updated_at=moment,
            ambiguous_attempt_seen=True,
            possibly_duplicated=duplicated,
            attempt_log=log,
        )
        self._store.save_record(recovered)
        return recovered

    def _recover_in_flight(self) -> None:
        for record in self._store.all_records():
            if record.state is OutboxState.IN_FLIGHT:
                self._recover_record(record)

    # -- helpers ------------------------------------------------------------------------

    def _cooldown_elapsed(self, record: OutboxRecord, now: datetime) -> bool:
        if record.last_attempt_at is None:
            return True
        elapsed = (now - ensure_utc(record.last_attempt_at)).total_seconds()
        return elapsed >= self._config.cooldown_seconds

    def _payload_from_record(self, record: OutboxRecord) -> TransportPayload:
        return TransportPayload(
            delivery_id=record.delivery_id,
            message_id=record.message_id,
            signal_id=record.signal_id,
            destination_id=record.destination_id,
            attempt_number=record.attempts_used + 1,
            rendered=record.payload.to_rendered(record.signal_id, record.message_id),
        )

    def _receipt_from_record(self, record: OutboxRecord) -> DeliveryReceipt:
        """Rebuild the durable receipt of a terminal record (no transport call).

        Deterministic fallbacks cover a restart-recovered ``AMBIGUOUS`` record
        whose attempt never produced a receipt: the outcome is ambiguous, so
        ``UNKNOWN``/``TIMEOUT`` is the honest representation; any other
        terminal without a stored receipt reports ``FAILED``/``TRANSPORT``.
        """
        state = record.last_receipt_state
        category = record.last_failure_category
        if state is None:
            if record.state is OutboxState.AMBIGUOUS:
                state = DeliveryState.UNKNOWN
                category = FailureCategory.TIMEOUT
            elif record.state is OutboxState.DELIVERED:
                state = DeliveryState.DELIVERED
                category = FailureCategory.NONE
            else:
                state = DeliveryState.FAILED
                category = FailureCategory.TRANSPORT
        if category is None:
            category = FailureCategory.NONE
        return DeliveryReceipt(
            delivery_id=record.delivery_id,
            message_id=record.message_id,
            signal_id=record.signal_id,
            destination_id=record.destination_id,
            attempt_number=max(1, record.attempts_used),
            state=state,
            failure_category=category,
        )
