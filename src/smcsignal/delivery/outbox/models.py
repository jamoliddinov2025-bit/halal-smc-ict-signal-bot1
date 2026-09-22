"""Phase 35D durable delivery outbox: records, states, and configuration.

This module defines the durable vocabulary of the outbox and nothing else:
the seven lifecycle states, the immutable record schema
``smcsignal.outbox.record/v1``, the meta schema ``smcsignal.outbox.meta/v1``,
and the strict operator configuration. Identity is never invented here: every
record is keyed by the frozen deterministic ``delivery_id`` computed upstream
by ``smcsignal.delivery.identity`` — a content digest over stable inputs (no
clock, no randomness, no process identity), and therefore stable across
restarts. Wall-clock fields are operational metadata only (the same precedent
as ``DeliveryReceipt.attempted_at``) and never enter any identity.

No network, no scheduling, no thread, and no second transport lives here;
this module is pure immutable records plus strict validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.models import (
    ChartAttachment,
    DeliveryState,
    FailureCategory,
    RenderedSignalMessage,
    chart_artifact_id,
)
from smcsignal.delivery.transport import TransportPayload

RECORD_SCHEMA = "smcsignal.outbox.record/v1"
META_SCHEMA = "smcsignal.outbox.meta/v1"

DEFAULT_MAX_ATTEMPTS = 5
MIN_MAX_ATTEMPTS = 1
MAX_MAX_ATTEMPTS = 10
DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_MAX_PER_DRAIN = 8

_REQUIRED_RECORD_FIELDS = frozenset(
    {
        "schema",
        "delivery_id",
        "message_id",
        "signal_id",
        "destination_id",
        "state",
        "attempts_used",
        "max_attempts",
        "created_at",
        "updated_at",
        "last_attempt_at",
        "last_receipt_state",
        "last_failure_category",
        "ambiguous_attempt_seen",
        "possibly_duplicated",
        "attempt_log",
        "payload",
    }
)
_REQUIRED_PAYLOAD_FIELDS = frozenset({"caption", "parts", "chart"})
_REQUIRED_CHART_FIELDS = frozenset(
    {"signal_id", "drawing_id", "mime_type", "filename", "content", "artifact_id"}
)
_REQUIRED_ATTEMPT_FIELDS = frozenset({"attempt", "receipt_state", "failure_category", "at"})
_REQUIRED_META_FIELDS = frozenset({"schema", "activated_at"})


class OutboxState(StrEnum):
    """Durable lifecycle of one delivery intent, keyed by ``delivery_id``.

    ``QUEUED`` — the intent is durable and zero attempts occurred; no call
    could have reached the transport. ``IN_FLIGHT`` — attempt N started and
    was persisted *before* the transport call, so a crash here must be
    recovered as an ambiguous attempt. ``WAITING`` — the last attempt ended
    non-terminally (retryable failure or ambiguity) with budget remaining.
    ``DELIVERED`` — terminal success (a frozen ``DELIVERED`` or ``SENT``
    receipt). ``FAILED_TERMINAL`` — terminal, non-retryable receipt category.
    ``EXHAUSTED`` — budget consumed with the last receipt a retryable failure.
    ``AMBIGUOUS`` — budget consumed with the last outcome ambiguous; the
    durable representation of an unresolved duplicate/loss question.
    """

    QUEUED = "QUEUED"
    IN_FLIGHT = "IN_FLIGHT"
    WAITING = "WAITING"
    DELIVERED = "DELIVERED"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    EXHAUSTED = "EXHAUSTED"
    AMBIGUOUS = "AMBIGUOUS"


TERMINAL_STATES: frozenset[OutboxState] = frozenset(
    {
        OutboxState.DELIVERED,
        OutboxState.FAILED_TERMINAL,
        OutboxState.EXHAUSTED,
        OutboxState.AMBIGUOUS,
    }
)

#: Frozen ``FailureCategory`` values whose ``FAILED`` receipt may be retried
#: by the durable budget. Configuration/build/chart faults never self-heal and
#: are therefore terminal without consuming further attempts.
RETRYABLE_FAILURE_CATEGORIES: frozenset[FailureCategory] = frozenset(
    {
        FailureCategory.TRANSPORT,
        FailureCategory.NETWORK,
        FailureCategory.RATE_LIMIT,
    }
)


def receipt_allows_retry(state: DeliveryState, category: FailureCategory) -> bool:
    """Locked retryability of one frozen receipt (pure, deterministic).

    ``UNKNOWN`` is retryable under the durable budget regardless of category
    (an ambiguous outcome, never a confirmed failure). ``FAILED`` is retryable
    exactly for the transport/network/rate-limit categories. Every other
    receipt — ``NOT_ATTEMPTED`` included — is non-retryable.
    """
    if not isinstance(state, DeliveryState):
        raise AnalysisInputError("receipt_allows_retry requires a DeliveryState")
    if not isinstance(category, FailureCategory):
        raise AnalysisInputError("receipt_allows_retry requires a FailureCategory")
    if state is DeliveryState.UNKNOWN:
        return True
    if state is DeliveryState.FAILED:
        return category in RETRYABLE_FAILURE_CATEGORIES
    return False


def ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC (operational metadata only)."""
    if not isinstance(value, datetime):
        raise AnalysisInputError("a datetime is required")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_iso(value: datetime) -> str:
    """Serialize an aware datetime as ISO-8601 text."""
    return ensure_utc(value).isoformat()


def parse_iso(value: object, name: str) -> datetime:
    """Parse ISO-8601 text into an aware UTC datetime (strict)."""
    if not isinstance(value, str) or not value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AnalysisInputError(f"{name} is not valid ISO-8601: {value!r}") from exc
    return ensure_utc(parsed)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def _prefixed(value: object, name: str, prefix: str) -> str:
    text = _text(value, name)
    if not text.startswith(prefix):
        raise AnalysisInputError(f"{name} must carry the {prefix} prefix")
    return text


def _bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise AnalysisInputError(f"{name} must be a boolean")
    return value


def _count(value: object, name: str, *, minimum: int) -> int:
    if type(value) is not int or isinstance(value, bool) or value < minimum:
        raise AnalysisInputError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class OutboxConfig:
    """Strict durable-outbox policy; none of it can reach signal generation.

    ``max_attempts`` is the total per-delivery attempt budget (the first
    delivery counts as attempt 1); the locked default is 5 and the allowed
    range is 1-10. ``cooldown_seconds`` is the minimum delay between two
    attempts of one record during draining. ``max_per_drain`` caps how many
    records one drain pass may attempt.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    max_per_drain: int = DEFAULT_MAX_PER_DRAIN

    def __post_init__(self) -> None:
        attempts = self.max_attempts
        if (
            type(attempts) is not int
            or isinstance(attempts, bool)
            or not MIN_MAX_ATTEMPTS <= attempts <= MAX_MAX_ATTEMPTS
        ):
            raise AnalysisInputError(
                f"max_attempts must be an integer from {MIN_MAX_ATTEMPTS} to {MAX_MAX_ATTEMPTS}"
            )
        cooldown = self.cooldown_seconds
        if (
            not isinstance(cooldown, (int, float))
            or isinstance(cooldown, bool)
            or not math.isfinite(cooldown)
            or cooldown < 0
        ):
            raise AnalysisInputError("cooldown_seconds must be a finite number >= 0")
        per_drain = self.max_per_drain
        if type(per_drain) is not int or isinstance(per_drain, bool) or per_drain < 1:
            raise AnalysisInputError("max_per_drain must be a positive integer")


@dataclass(frozen=True, slots=True)
class AttemptEntry:
    """One durable attempt outcome inside a record's bounded attempt log."""

    attempt: int
    receipt_state: DeliveryState
    failure_category: FailureCategory
    at: datetime

    def __post_init__(self) -> None:
        _count(self.attempt, "attempt", minimum=1)
        if not isinstance(self.receipt_state, DeliveryState):
            raise AnalysisInputError("attempt entry requires a DeliveryState")
        if not isinstance(self.failure_category, FailureCategory):
            raise AnalysisInputError("attempt entry requires a FailureCategory")
        if not isinstance(self.at, datetime):
            raise AnalysisInputError("attempt entry requires a datetime")

    def to_document(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "receipt_state": self.receipt_state.value,
            "failure_category": self.failure_category.value,
            "at": format_iso(self.at),
        }

    @classmethod
    def from_document(cls, document: object) -> AttemptEntry:
        if not isinstance(document, dict) or set(document) != _REQUIRED_ATTEMPT_FIELDS:
            raise AnalysisInputError(
                "attempt log entry must contain exactly: "
                + ", ".join(sorted(_REQUIRED_ATTEMPT_FIELDS))
            )
        return cls(
            attempt=_count(document["attempt"], "attempt", minimum=1),
            receipt_state=_delivery_state(document["receipt_state"], "receipt_state"),
            failure_category=_failure_category(document["failure_category"], "failure_category"),
            at=parse_iso(document["at"], "attempt at"),
        )


def _delivery_state(value: object, name: str) -> DeliveryState:
    if not isinstance(value, str):
        raise AnalysisInputError(f"{name} must be a string")
    try:
        return DeliveryState(value)
    except ValueError as exc:
        raise AnalysisInputError(f"{name} is not a DeliveryState value: {value!r}") from exc


def _failure_category(value: object, name: str) -> FailureCategory:
    if not isinstance(value, str):
        raise AnalysisInputError(f"{name} must be a string")
    try:
        return FailureCategory(value)
    except ValueError as exc:
        raise AnalysisInputError(f"{name} is not a FailureCategory value: {value!r}") from exc


def _outbox_state(value: object, name: str) -> OutboxState:
    if not isinstance(value, str):
        raise AnalysisInputError(f"{name} must be a string")
    try:
        return OutboxState(value)
    except ValueError as exc:
        raise AnalysisInputError(f"{name} is not an OutboxState value: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class OutboxPayload:
    """The durable, already-rendered content of one delivery intent.

    Storing the frozen rendered caption/parts/chart makes every retry and
    every reconciled re-enqueue self-contained: no runtime, analyzer, or
    re-rendering is needed to attempt the delivery again, and the bytes sent
    are exactly the bytes that were rendered the first time.
    """

    caption: str
    parts: tuple[str, ...]
    chart: ChartAttachment | None

    def __post_init__(self) -> None:
        _text(self.caption, "caption")
        if (
            not isinstance(self.parts, tuple)
            or not self.parts
            or not all(isinstance(part, str) and part.strip() for part in self.parts)
        ):
            raise AnalysisInputError("parts must be a nonempty tuple of nonempty strings")
        if self.chart is not None and not isinstance(self.chart, ChartAttachment):
            raise AnalysisInputError("chart must be a ChartAttachment or None")

    @classmethod
    def from_transport_payload(cls, payload: TransportPayload) -> OutboxPayload:
        if not isinstance(payload, TransportPayload):
            raise AnalysisInputError("from_transport_payload requires a TransportPayload")
        return cls(
            caption=payload.rendered.caption,
            parts=payload.rendered.parts,
            chart=payload.rendered.chart,
        )

    def to_rendered(self, signal_id: str, message_id: str) -> RenderedSignalMessage:
        """Rebuild the frozen rendered message exactly as first produced."""
        return RenderedSignalMessage(
            signal_id=signal_id,
            message_id=message_id,
            caption=self.caption,
            parts=self.parts,
            chart=self.chart,
        )

    def to_document(self) -> dict[str, object]:
        chart: dict[str, object] | None = None
        if self.chart is not None:
            chart = {
                "signal_id": self.chart.signal_id,
                "drawing_id": self.chart.drawing_id,
                "mime_type": self.chart.mime_type,
                "filename": self.chart.filename,
                "content": self.chart.content,
                "artifact_id": self.chart.artifact_id,
            }
        return {"caption": self.caption, "parts": list(self.parts), "chart": chart}

    @classmethod
    def from_document(cls, document: object) -> OutboxPayload:
        if not isinstance(document, dict) or set(document) != _REQUIRED_PAYLOAD_FIELDS:
            raise AnalysisInputError(
                "payload must contain exactly: " + ", ".join(sorted(_REQUIRED_PAYLOAD_FIELDS))
            )
        caption = _text(document["caption"], "payload caption")
        raw_parts = document["parts"]
        if not isinstance(raw_parts, list) or not all(isinstance(item, str) for item in raw_parts):
            raise AnalysisInputError("payload parts must be a list of strings")
        chart_document = document["chart"]
        chart: ChartAttachment | None = None
        if chart_document is not None:
            if (
                not isinstance(chart_document, dict)
                or set(chart_document) != _REQUIRED_CHART_FIELDS
            ):
                raise AnalysisInputError(
                    "chart must be null or contain exactly: "
                    + ", ".join(sorted(_REQUIRED_CHART_FIELDS))
                )
            signal_id = _text(chart_document["signal_id"], "chart signal_id")
            drawing_id = _prefixed(chart_document["drawing_id"], "chart drawing_id", "drawing:")
            mime_type = _text(chart_document["mime_type"], "chart mime_type")
            raw_content = chart_document["content"]
            # Content matches the frozen ChartAttachment rule: nonempty string,
            # never trimmed (rendered artifacts end with their own whitespace).
            if not isinstance(raw_content, str) or not raw_content:
                raise AnalysisInputError("chart content must be a nonempty string")
            chart = ChartAttachment(
                signal_id=signal_id,
                drawing_id=drawing_id,
                mime_type=mime_type,
                filename=_text(chart_document["filename"], "chart filename"),
                content=raw_content,
                artifact_id=_text(chart_document["artifact_id"], "chart artifact_id"),
            )
            expected = chart_artifact_id(signal_id, drawing_id, mime_type)
            if chart.artifact_id != expected:
                raise AnalysisInputError(
                    "chart artifact_id does not match its deterministic identity"
                )
        return cls(caption=caption, parts=tuple(raw_parts), chart=chart)


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """One durable delivery intent keyed by the frozen ``delivery_id``.

    The record is the single durable authority for what happened to one
    delivery of one signal to one destination: its attempt budget, every
    attempt's outcome, and its terminal or in-progress lifecycle state. It is
    rewritten atomically on every transition; readers observe either the
    complete previous record or the complete new one.
    """

    delivery_id: str
    message_id: str
    signal_id: str
    destination_id: str
    state: OutboxState
    attempts_used: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    last_attempt_at: datetime | None
    last_receipt_state: DeliveryState | None
    last_failure_category: FailureCategory | None
    ambiguous_attempt_seen: bool
    possibly_duplicated: bool
    attempt_log: tuple[AttemptEntry, ...]
    payload: OutboxPayload

    def __post_init__(self) -> None:
        _prefixed(self.delivery_id, "delivery_id", "delivery:")
        _prefixed(self.message_id, "message_id", "message:")
        _text(self.signal_id, "signal_id")
        _text(self.destination_id, "destination_id")
        if not isinstance(self.state, OutboxState):
            raise AnalysisInputError("record state must be an OutboxState")
        _count(self.attempts_used, "attempts_used", minimum=0)
        maximum = _count(self.max_attempts, "max_attempts", minimum=MIN_MAX_ATTEMPTS)
        if self.attempts_used > maximum:
            raise AnalysisInputError("attempts_used cannot exceed max_attempts")
        if not isinstance(self.created_at, datetime) or not isinstance(self.updated_at, datetime):
            raise AnalysisInputError("created_at and updated_at must be datetimes")
        if self.last_attempt_at is not None and not isinstance(self.last_attempt_at, datetime):
            raise AnalysisInputError("last_attempt_at must be a datetime or None")
        if self.last_receipt_state is not None and not isinstance(
            self.last_receipt_state, DeliveryState
        ):
            raise AnalysisInputError("last_receipt_state must be a DeliveryState or None")
        if self.last_failure_category is not None and not isinstance(
            self.last_failure_category, FailureCategory
        ):
            raise AnalysisInputError("last_failure_category must be a FailureCategory or None")
        _bool(self.ambiguous_attempt_seen, "ambiguous_attempt_seen")
        _bool(self.possibly_duplicated, "possibly_duplicated")
        if not isinstance(self.attempt_log, tuple) or not all(
            isinstance(entry, AttemptEntry) for entry in self.attempt_log
        ):
            raise AnalysisInputError("attempt_log must be a tuple of AttemptEntry values")
        if len(self.attempt_log) > maximum:
            raise AnalysisInputError("attempt_log cannot exceed max_attempts entries")
        if any(entry.attempt != index + 1 for index, entry in enumerate(self.attempt_log)):
            raise AnalysisInputError("attempt_log entries must be sequential from 1")
        if not isinstance(self.payload, OutboxPayload):
            raise AnalysisInputError("record payload must be an OutboxPayload")
        if self.payload.chart is not None and self.payload.chart.signal_id != self.signal_id:
            raise AnalysisInputError("chart payload must reference this exact signal")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def budget_remaining(self) -> bool:
        return self.attempts_used < self.max_attempts

    def to_document(self) -> dict[str, object]:
        return {
            "schema": RECORD_SCHEMA,
            "delivery_id": self.delivery_id,
            "message_id": self.message_id,
            "signal_id": self.signal_id,
            "destination_id": self.destination_id,
            "state": self.state.value,
            "attempts_used": self.attempts_used,
            "max_attempts": self.max_attempts,
            "created_at": format_iso(self.created_at),
            "updated_at": format_iso(self.updated_at),
            "last_attempt_at": (
                format_iso(self.last_attempt_at) if self.last_attempt_at is not None else None
            ),
            "last_receipt_state": (
                self.last_receipt_state.value if self.last_receipt_state is not None else None
            ),
            "last_failure_category": (
                self.last_failure_category.value if self.last_failure_category is not None else None
            ),
            "ambiguous_attempt_seen": self.ambiguous_attempt_seen,
            "possibly_duplicated": self.possibly_duplicated,
            "attempt_log": [entry.to_document() for entry in self.attempt_log],
            "payload": self.payload.to_document(),
        }

    @classmethod
    def from_document(cls, document: object) -> OutboxRecord:
        if not isinstance(document, dict) or set(document) != _REQUIRED_RECORD_FIELDS:
            raise AnalysisInputError(
                "outbox record must contain exactly: " + ", ".join(sorted(_REQUIRED_RECORD_FIELDS))
            )
        if document["schema"] != RECORD_SCHEMA:
            raise AnalysisInputError(f"unsupported outbox record schema: {document['schema']!r}")
        raw_state = document["last_receipt_state"]
        raw_category = document["last_failure_category"]
        raw_attempt_log = document["attempt_log"]
        if not isinstance(raw_attempt_log, list):
            raise AnalysisInputError("attempt_log must be a list")
        return cls(
            delivery_id=_prefixed(document["delivery_id"], "delivery_id", "delivery:"),
            message_id=_prefixed(document["message_id"], "message_id", "message:"),
            signal_id=_text(document["signal_id"], "signal_id"),
            destination_id=_text(document["destination_id"], "destination_id"),
            state=_outbox_state(document["state"], "state"),
            attempts_used=_count(document["attempts_used"], "attempts_used", minimum=0),
            max_attempts=_count(document["max_attempts"], "max_attempts", minimum=MIN_MAX_ATTEMPTS),
            created_at=parse_iso(document["created_at"], "created_at"),
            updated_at=parse_iso(document["updated_at"], "updated_at"),
            last_attempt_at=(
                None
                if document["last_attempt_at"] is None
                else parse_iso(document["last_attempt_at"], "last_attempt_at")
            ),
            last_receipt_state=(
                None if raw_state is None else _delivery_state(raw_state, "last_receipt_state")
            ),
            last_failure_category=(
                None
                if raw_category is None
                else _failure_category(raw_category, "last_failure_category")
            ),
            ambiguous_attempt_seen=_bool(
                document["ambiguous_attempt_seen"], "ambiguous_attempt_seen"
            ),
            possibly_duplicated=_bool(document["possibly_duplicated"], "possibly_duplicated"),
            attempt_log=tuple(AttemptEntry.from_document(entry) for entry in raw_attempt_log),
            payload=OutboxPayload.from_document(document["payload"]),
        )


def new_queued_record(
    payload: TransportPayload,
    *,
    now: datetime,
    max_attempts: int,
    ambiguous_attempt_seen: bool = False,
) -> OutboxRecord:
    """Create the durable ``QUEUED`` intent for one transport payload (T1).

    ``ambiguous_attempt_seen`` starts true only when reconciliation recreates
    an intent whose previous record was quarantined — the prior attempt
    history is unknowable, so the conservative flag is carried from birth.
    """
    if not isinstance(payload, TransportPayload):
        raise AnalysisInputError("new_queued_record requires a TransportPayload")
    moment = ensure_utc(now)
    return OutboxRecord(
        delivery_id=payload.delivery_id,
        message_id=payload.message_id,
        signal_id=payload.signal_id,
        destination_id=payload.destination_id,
        state=OutboxState.QUEUED,
        attempts_used=0,
        max_attempts=max_attempts,
        created_at=moment,
        updated_at=moment,
        last_attempt_at=None,
        last_receipt_state=None,
        last_failure_category=None,
        ambiguous_attempt_seen=_bool(ambiguous_attempt_seen, "ambiguous_attempt_seen"),
        possibly_duplicated=False,
        attempt_log=(),
        payload=OutboxPayload.from_transport_payload(payload),
    )


def parse_meta_document(document: object) -> datetime:
    """Parse a ``smcsignal.outbox.meta/v1`` document into ``activated_at``."""
    if not isinstance(document, dict) or set(document) != _REQUIRED_META_FIELDS:
        raise AnalysisInputError(
            "outbox meta must contain exactly: " + ", ".join(sorted(_REQUIRED_META_FIELDS))
        )
    if document["schema"] != META_SCHEMA:
        raise AnalysisInputError(f"unsupported outbox meta schema: {document['schema']!r}")
    return parse_iso(document["activated_at"], "activated_at")


def meta_document(now: datetime) -> dict[str, object]:
    """Build the bootstrap meta document for one outbox store."""
    return {"schema": META_SCHEMA, "activated_at": format_iso(now)}
