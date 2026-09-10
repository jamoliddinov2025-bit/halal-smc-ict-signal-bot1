"""Phase 24D-B transport-facing payload foundation (offline, additive).

This module resolves Open Decision D1 of the Phase 24D-A design: the frozen
``MessageSink`` hands a sink only a ``DeliveryAttempt``, which carries identity
and routing but **not** the rendered content a real transport must send. The
rendered payload already exists on the frozen coordinator's ``DeliveryOutcome``
(``outcome.rendered``). This module makes that payload available to future
transports through a deterministic, immutable ``TransportPayload`` value and a
``PayloadSink`` capability, without re-rendering and without modifying any
Phase 24B/24C module.

Everything here is offline and additive. No network, no HTTP, no secrets, no
transport implementation. The real remote transport (Phase 24D-C) will be a
``PayloadSink`` fed by the coordinator's ``DeliveryOutcome`` through this value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smcsignal.analysis.errors import AnalysisInputError

from .models import (
    ChartAttachment,
    DeliveryReceipt,
    DeliveryState,
    FailureCategory,
    RenderedSignalMessage,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from .orchestrator import DeliveryOutcome


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class TransportPayload:
    """Immutable, transport-facing projection of one rendered delivery.

    Carries the existing delivery metadata (the same ids the frozen
    ``DeliveryAttempt``/``DeliveryReceipt`` already carry) together with the
    already-rendered message content (text ``caption``/``parts`` and optional
    chart ``ChartAttachment``). No content is re-rendered here and no field is
    invented: the rendered content is the frozen ``RenderedSignalMessage``.

    A future transport reads this value and returns a ``DeliveryReceipt``; it
    never needs to call back into presentation or orchestration to obtain the
    payload.
    """

    delivery_id: str
    message_id: str
    signal_id: str
    destination_id: str
    attempt_number: int
    rendered: RenderedSignalMessage

    def __post_init__(self) -> None:
        _text(self.delivery_id, "delivery_id")
        if not self.delivery_id.startswith("delivery:"):
            raise AnalysisInputError("delivery_id must carry the delivery: prefix")
        _text(self.message_id, "message_id")
        if not self.message_id.startswith("message:"):
            raise AnalysisInputError("message_id must carry the message: prefix")
        _text(self.signal_id, "signal_id")
        _text(self.destination_id, "destination_id")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise AnalysisInputError("attempt_number must be a positive integer")
        if not isinstance(self.rendered, RenderedSignalMessage):
            raise AnalysisInputError("rendered must be a RenderedSignalMessage")
        # Consistency: the payload must never contradict its rendered content.
        if self.rendered.signal_id != self.signal_id:
            raise AnalysisInputError("rendered message must reference this exact signal")
        if self.rendered.message_id != self.message_id:
            raise AnalysisInputError("rendered message_id must equal the payload message_id")

    @property
    def caption(self) -> str:
        """Deterministic rendered text of this delivery."""
        return self.rendered.caption

    @property
    def parts(self) -> tuple[str, ...]:
        """Caption split at safe boundaries for transport message limits."""
        return self.rendered.parts

    @property
    def chart(self) -> ChartAttachment | None:
        """Optional bound chart artifact (None when chart failed or absent)."""
        return self.rendered.chart

    @property
    def chart_present(self) -> bool:
        """True when an optional chart artifact is bound to this delivery."""
        return self.rendered.chart is not None


def transport_payload_from_outcome(outcome: DeliveryOutcome) -> TransportPayload:
    """Project an immutable transport payload from a frozen ``DeliveryOutcome``.

    Reads the outcome's deterministic delivery metadata and its already-rendered
    ``RenderedSignalMessage``; it never re-renders a signal and never mutates
    the outcome.
    """
    from .orchestrator import DeliveryOutcome

    if not isinstance(outcome, DeliveryOutcome):
        raise AnalysisInputError("transport_payload_from_outcome requires a DeliveryOutcome")
    return TransportPayload(
        delivery_id=outcome.delivery_id,
        message_id=outcome.message_id,
        signal_id=outcome.signal_id,
        destination_id=outcome.destination_id,
        attempt_number=outcome.attempt_number,
        rendered=outcome.rendered,
    )


class PayloadSink:
    """Transport-facing capability: deliver one immutable transport payload.

    Future concrete transports (Phase 24D-C) implement ``deliver_payload`` to
    send ``TransportPayload.caption``/``parts`` and the optional
    ``TransportPayload.chart`` to the destination, then return a
    ``DeliveryReceipt`` in the frozen ``DeliveryState``/``FailureCategory``
    vocabulary. This is an additive contract; it does not replace the frozen
    ``MessageSink`` used by ``NullSink``/``FakeTransportSink``.
    """

    def deliver_payload(self, payload: TransportPayload) -> DeliveryReceipt:  # pragma: no cover
        raise NotImplementedError


@dataclass
class OfflinePayloadSink:
    """Deterministic offline PayloadSink for tests and offline demonstration.

    Records each received payload and returns a configurable receipt, exactly
    like ``FakeTransportSink`` does for the frozen ``MessageSink`` path. No
    network activity ever occurs.
    """

    outcomes: list[DeliveryState] = field(default_factory=lambda: [DeliveryState.DELIVERED])
    failure_category: FailureCategory = FailureCategory.NONE
    seen: list[TransportPayload] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.outcomes or not all(
            isinstance(state, DeliveryState) for state in self.outcomes
        ):
            raise AnalysisInputError("outcomes must be a nonempty list of DeliveryState")
        if not isinstance(self.failure_category, FailureCategory):
            raise AnalysisInputError("failure_category must be a FailureCategory")

    def deliver_payload(self, payload: TransportPayload) -> DeliveryReceipt:
        if not isinstance(payload, TransportPayload):
            raise AnalysisInputError("OfflinePayloadSink requires a TransportPayload")
        self.seen.append(payload)
        index = min(payload.attempt_number - 1, len(self.outcomes) - 1)
        state = self.outcomes[index]
        category = (
            self.failure_category if state is not DeliveryState.DELIVERED else FailureCategory.NONE
        )
        return DeliveryReceipt(
            delivery_id=payload.delivery_id,
            message_id=payload.message_id,
            signal_id=payload.signal_id,
            destination_id=payload.destination_id,
            attempt_number=payload.attempt_number,
            state=state,
            failure_category=category,
        )
