"""Phase 24D-D Telegram coordinator integration (downstream-only, offline-testable).

This module wires the Phase 24D-B payload foundation and the Phase 24D-C
``TelegramSink`` into the frozen Phase 24C delivery orchestration. It is the
"24D driver" the Phase 24D-A design specifies (§2.1/§2.2, §24 item 3).

The contract-evolution reality this driver exists to bridge
----------------------------------------------------------

The frozen ``MessageSink.deliver(attempt: DeliveryAttempt)`` boundary carries
**identity only** — no caption, no bytes. A real remote transport must send
content, which lives on the coordinator's ``DeliveryOutcome.rendered``. Phase
24D-B therefore added a second, payload-aware boundary (``TransportPayload`` /
``PayloadSink`` / ``transport_payload_from_outcome``). This module connects the
two without modifying either:

1. ``TelegramPayloadBridge`` implements the frozen ``MessageSink`` protocol and
   forwards each attempt to an injected ``PayloadSink``, using the already
   rendered payload the driver supplied. It never renders and never invents
   content.
2. ``TelegramDeliveryIntegration`` obtains the payload from the frozen
   coordinator (used in projection mode), projects it with the Phase 24D-B
   ``transport_payload_from_outcome``, and drives the frozen ``deliver_message``
   retry/dedup loop over the bridge so the coordinator's deterministic identity,
   dedup registry, retry cap, and receipt semantics are preserved exactly.

Because the frozen retry/dedup loop is reused rather than reimplemented, and
because the bridge returns the transport's own receipt unchanged, every
``DeliveryState``/``FailureCategory`` value keeps its frozen meaning. Retries
compose with the transport's own internal bounded retry: the driver retries only
after a ``FAILED`` receipt, which ``TelegramSink`` returns only once its own
transport-level budget is exhausted (§10's two non-overlapping retry scopes).

Strictly downstream and output-only. Nothing here can influence signal
generation, rendering, halal classification, eligibility, governance, strategy,
risk, or execution. There is no polling, no webhook, no inbound message
processing, no Telegram command handling, no database, and no multi-channel
fan-out: one call delivers one already-published signal to one logical
destination.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from smcsignal.analysis.errors import AnalysisInputError

from ..config import DeliveryConfig
from ..models import (
    DeliveryAttempt,
    DeliveryReceipt,
    DeliveryState,
    FailureCategory,
)
from ..orchestrator import DeliveryCoordinator, DeliveryOutcome
from ..sink import DeliveryRegistry, deliver_message
from ..transport import PayloadSink, TransportPayload, transport_payload_from_outcome
from .audit import TelegramAuditRecord, telegram_audit_record

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from smcsignal.analysis.setup_attribution.models import SetupAttribution
    from smcsignal.analysis.signal_engine.models import SignalSnapshot
    from smcsignal.analysis.visualization import DrawingModel

    from ..orchestrator import OrchestrationConfig

_STATE_FIELD: dict[DeliveryState, str] = {
    DeliveryState.DELIVERED: "delivered",
    DeliveryState.SENT: "sent",
    DeliveryState.UNKNOWN: "unknown",
    DeliveryState.FAILED: "failed",
    DeliveryState.NOT_ATTEMPTED: "not_attempted",
    DeliveryState.SKIPPED_DUPLICATE: "skipped_duplicate",
}


@dataclass(frozen=True, slots=True)
class TelegramDeliveryCounters:
    """Immutable, deterministic per-destination tally of delivery states.

    Pure bookkeeping for operational visibility (Phase 24D-D, §24 item 3). It
    never influences delivery: recording a state cannot change a receipt, a
    retry decision, or any upstream fact.
    """

    delivered: int = 0
    sent: int = 0
    unknown: int = 0
    failed: int = 0
    not_attempted: int = 0
    skipped_duplicate: int = 0

    def __post_init__(self) -> None:
        for name in _STATE_FIELD.values():
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise AnalysisInputError(f"{name} must be a nonnegative integer")

    def record(self, state: DeliveryState) -> TelegramDeliveryCounters:
        """Return a new tally with one more delivery in ``state``."""
        if not isinstance(state, DeliveryState):
            raise AnalysisInputError("record requires a DeliveryState")
        name = _STATE_FIELD[state]
        return replace(self, **{name: getattr(self, name) + 1})

    @property
    def total(self) -> int:
        return sum(getattr(self, name) for name in _STATE_FIELD.values())

    @property
    def delivered_total(self) -> int:
        """Deliveries the transport reported as ``DELIVERED`` or ``SENT``."""
        return self.delivered + self.sent


class TelegramPayloadBridge:
    """Adapts the frozen ``MessageSink`` boundary onto a payload-aware transport.

    The bridge receives the already-rendered ``TransportPayload`` from the driver
    (``bind_payload``) and hands it to the injected ``PayloadSink`` when the
    frozen coordinator calls ``deliver(attempt)``. It performs three checks and
    no transformation: it refuses to send without a bound payload, refuses a
    payload whose identity contradicts the attempt, and passes the attempt number
    the frozen retry loop is currently on so the transport's receipt reflects the
    real attempt. It never renders, never mutates caption content, and never adds
    retry or state logic of its own.
    """

    def __init__(self, sink: PayloadSink) -> None:
        if sink is None:
            raise AnalysisInputError("TelegramPayloadBridge requires a payload sink")
        self._sink = sink
        self._bound: TransportPayload | None = None

    @property
    def sink(self) -> PayloadSink:
        return self._sink

    @property
    def bound_payload(self) -> TransportPayload | None:
        return self._bound

    def bind_payload(self, payload: TransportPayload) -> None:
        """Supply the already-rendered payload the next attempt must send."""
        if not isinstance(payload, TransportPayload):
            raise AnalysisInputError("bind_payload requires a TransportPayload")
        self._bound = payload

    def release(self) -> None:
        """Drop the bound payload so a stale one can never be re-sent."""
        self._bound = None

    def deliver(self, attempt: DeliveryAttempt) -> DeliveryReceipt:
        """Forward one attempt to the transport with its bound payload."""
        if not isinstance(attempt, DeliveryAttempt):
            raise AnalysisInputError("TelegramPayloadBridge.deliver requires a DeliveryAttempt")
        bound = self._bound
        if bound is None:
            raise AnalysisInputError("TelegramPayloadBridge has no bound payload")
        if bound.delivery_id != attempt.delivery_id:
            raise AnalysisInputError("bound payload does not match the delivery attempt")
        if bound.message_id != attempt.message_id or bound.signal_id != attempt.signal_id:
            raise AnalysisInputError("bound payload identity does not match the delivery attempt")
        if bound.destination_id != attempt.destination_id:
            raise AnalysisInputError("bound payload destination does not match the attempt")
        payload = (
            bound
            if bound.attempt_number == attempt.attempt_number
            else replace(bound, attempt_number=attempt.attempt_number)
        )
        return self._sink.deliver_payload(payload)


@dataclass(frozen=True, slots=True)
class TelegramDeliveryResult:
    """One integrated delivery: the projected outcome, payload, and receipt.

    ``receipt`` is the transport's receipt and is authoritative for the outcome
    of the send; ``outcome`` is the frozen coordinator envelope the payload was
    projected from (carrying the deterministic ids and the already-rendered
    content). ``payload`` is ``None`` only when no payload reached the transport:
    a duplicate the registry already delivered, or a disabled orchestration
    master switch. A transport that refuses pre-flight (missing token, unknown
    destination, over-budget caption) still receives the payload and reports
    ``NOT_ATTEMPTED``; nothing is sent in that case either.
    """

    receipt: DeliveryReceipt
    outcome: DeliveryOutcome | None = None
    payload: TransportPayload | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, DeliveryReceipt):
            raise AnalysisInputError("receipt must be a DeliveryReceipt")
        if self.outcome is not None and not isinstance(self.outcome, DeliveryOutcome):
            raise AnalysisInputError("outcome must be a DeliveryOutcome or None")
        if self.payload is not None and not isinstance(self.payload, TransportPayload):
            raise AnalysisInputError("payload must be a TransportPayload or None")
        if (
            self.payload is not None
            and self.outcome is not None
            and self.payload.delivery_id != self.outcome.delivery_id
        ):
            raise AnalysisInputError("payload must reference the outcome delivery id")

    @property
    def state(self) -> DeliveryState:
        return self.receipt.state

    @property
    def failure_category(self) -> FailureCategory:
        return self.receipt.failure_category

    @property
    def delivery_id(self) -> str:
        return self.receipt.delivery_id

    @property
    def signal_id(self) -> str:
        return self.receipt.signal_id

    @property
    def destination_id(self) -> str:
        return self.receipt.destination_id

    @property
    def attempt_number(self) -> int:
        return self.receipt.attempt_number

    @property
    def delivered(self) -> bool:
        """True when the transport reported ``DELIVERED`` or ``SENT``."""
        return self.receipt.state in (DeliveryState.DELIVERED, DeliveryState.SENT)

    @property
    def was_sent(self) -> bool:
        """True when the transport accepted the payload for an actual send.

        A payload forwarded to a transport that refuses pre-flight (missing
        token, over-budget caption) reports ``NOT_ATTEMPTED`` and was not sent,
        so this is stricter than ``payload is not None``.
        """
        return self.payload is not None and self.receipt.state is not DeliveryState.NOT_ATTEMPTED

    def audit_record(self, *, chat_id: str | None = None) -> TelegramAuditRecord:
        """Deterministic, redacted audit record for a delivery that was sent."""
        if self.payload is None:
            raise AnalysisInputError("no audit record exists for a delivery that was not sent")
        return telegram_audit_record(self.payload, self.receipt, chat_id=chat_id)


class TelegramDeliveryIntegration:
    """Thin, injected driver connecting orchestration to the Telegram transport.

    Every dependency is injected: the transport is any ``PayloadSink`` (the real
    ``TelegramSink`` or an offline double), the coordinator is any
    ``DeliveryCoordinator``, and the dedup registry and retry cap are supplied by
    the caller. The integration owns no network details and no Telegram
    specifics, so it is fully testable with fake transports and no network.
    """

    def __init__(
        self,
        sink: PayloadSink,
        *,
        coordinator: DeliveryCoordinator | None = None,
        registry: DeliveryRegistry | None = None,
        max_attempts: int = 1,
    ) -> None:
        if type(max_attempts) is not int or max_attempts < 1:
            raise AnalysisInputError("max_attempts must be a positive integer")
        self._bridge = TelegramPayloadBridge(sink)
        # Projection mode: the driver obtains the rendered payload from the
        # coordinator's outcome, so the coordinator itself is given no sink and
        # never touches the network. The single real send happens below through
        # the frozen retry/dedup loop over the bridge.
        self._coordinator = coordinator if coordinator is not None else DeliveryCoordinator(None)
        self._registry = registry if registry is not None else DeliveryRegistry()
        self._max_attempts = max_attempts
        self._counters: dict[str, TelegramDeliveryCounters] = {}

    @property
    def bridge(self) -> TelegramPayloadBridge:
        return self._bridge

    @property
    def coordinator(self) -> DeliveryCoordinator:
        return self._coordinator

    @property
    def registry(self) -> DeliveryRegistry:
        return self._registry

    @property
    def config(self) -> OrchestrationConfig:
        return self._coordinator.config

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def counters(self, destination_id: str) -> TelegramDeliveryCounters:
        """Immutable tally for one logical destination (zeroes when unseen)."""
        return self._counters.get(destination_id, TelegramDeliveryCounters())

    @property
    def counter_summary(self) -> Mapping[str, TelegramDeliveryCounters]:
        """Read-only, deterministically ordered snapshot of all tallies."""
        return {key: self._counters[key] for key in sorted(self._counters)}

    def deliver(
        self,
        frame: SignalSnapshot,
        destination_id: str,
        *,
        drawing: DrawingModel | None = None,
        attribution: SetupAttribution | None = None,
        config: DeliveryConfig | None = None,
    ) -> TelegramDeliveryResult:
        """Project one published BUY signal and send it to one destination.

        The coordinator performs the frozen projection (BUY boundary, render,
        deterministic ids); this method then projects the outcome to a
        ``TransportPayload`` and delivers it through the transport exactly once,
        unless the registry already delivered that ``delivery_id`` or the
        orchestration master switch is off.
        """
        outcome = self._coordinator.deliver(
            frame,
            destination_id,
            drawing=drawing,
            attribution=attribution,
            config=config,
        )
        if not self._coordinator.config.enabled:
            # The orchestration master switch is off: nothing is sent, and the
            # coordinator's deterministic NOT_ATTEMPTED is preserved verbatim.
            return self._finish(
                TelegramDeliveryResult(
                    receipt=DeliveryReceipt(
                        delivery_id=outcome.delivery_id,
                        message_id=outcome.message_id,
                        signal_id=outcome.signal_id,
                        destination_id=outcome.destination_id,
                        attempt_number=outcome.attempt_number,
                        state=DeliveryState.NOT_ATTEMPTED,
                        failure_category=FailureCategory.CONFIG,
                    ),
                    outcome=outcome,
                    payload=None,
                )
            )
        return self.deliver_outcome(outcome)

    def deliver_outcome(self, outcome: DeliveryOutcome) -> TelegramDeliveryResult:
        """Project an existing ``DeliveryOutcome`` and send it via the transport.

        Reuses the Phase 24D-B ``transport_payload_from_outcome`` projection and
        the frozen ``deliver_message`` retry/dedup loop, so identity, dedup,
        retry, and receipt semantics are the frozen ones.
        """
        if not isinstance(outcome, DeliveryOutcome):
            raise AnalysisInputError("deliver_outcome requires a DeliveryOutcome")
        payload = transport_payload_from_outcome(outcome)
        self._bridge.bind_payload(payload)
        try:
            receipt = deliver_message(
                outcome.rendered,
                outcome.destination_id,
                self._bridge,
                # Mirror the coordinator's own dedup switch so orchestration
                # configuration keeps its pinned meaning on this path too.
                registry=self._registry if self._coordinator.config.deduplicate else None,
                max_attempts=self._max_attempts,
            )
        finally:
            # Never leave a payload bound to the bridge after the send, so a
            # later call can never re-send stale content.
            self._bridge.release()
        sent_payload = None if receipt.state is DeliveryState.SKIPPED_DUPLICATE else payload
        return self._finish(
            TelegramDeliveryResult(receipt=receipt, outcome=outcome, payload=sent_payload)
        )

    def _finish(self, result: TelegramDeliveryResult) -> TelegramDeliveryResult:
        """Record the tally for a completed delivery without altering it."""
        current = self._counters.get(result.destination_id, TelegramDeliveryCounters())
        self._counters[result.destination_id] = current.record(result.state)
        return result
