"""Transport-independent delivery abstraction for Phase 24.

``MessageSink`` is the boundary between the pure presentation core and any
concrete transport. Phase 24B ships only offline implementations (``NullSink``
and ``FakeTransportSink``); a real remote messaging transport is deferred to
Phase 24D and must never be imported by the presentation layer.

Delivery identity is deterministic and bound to the immutable message identity
plus a logical destination id. No exactly-once guarantee is claimed: the
coordinator deduplicates already-``DELIVERED`` messages at-most-once and reports
``UNKNOWN`` when a transport cannot confirm an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.setup_attribution.models import SetupAttribution
from smcsignal.analysis.signal_engine.models import SignalSnapshot
from smcsignal.analysis.visualization import DrawingModel

from . import chart
from . import message as message_core
from .config import DeliveryConfig
from .identity import delivery_identity
from .message import SignalMessage
from .models import (
    ChartAttachment,
    DeliveryAttempt,
    DeliveryReceipt,
    DeliveryState,
    FailureCategory,
    RenderedSignalMessage,
)


class MessageSink(Protocol):
    """Deliver one immutable rendered message and report an outcome."""

    def deliver(self, attempt: DeliveryAttempt) -> DeliveryReceipt: ...


@dataclass
class NullSink:
    """Offline no-op sink used when delivery is disabled.

    Delivers nothing and reports ``NOT_ATTEMPTED``; it exists so the coordinator
    and tests can exercise the offline no-op path without any network or a real
    transport.
    """

    seen: list[DeliveryAttempt] = field(default_factory=list)

    def deliver(self, attempt: DeliveryAttempt) -> DeliveryReceipt:
        if not isinstance(attempt, DeliveryAttempt):
            raise AnalysisInputError("NullSink.deliver requires a DeliveryAttempt")
        self.seen.append(attempt)
        return _receipt(attempt, DeliveryState.NOT_ATTEMPTED)


@dataclass
class FakeTransportSink:
    """Deterministic offline transport for tests.

    Each attempt consumes the next outcome from ``outcomes`` (repeating the last
    entry once the list is exhausted), records the attempt, and returns a
    receipt without any network activity. This makes success, failure, and
    unknown-outcome paths reproducible.
    """

    outcomes: list[DeliveryState] = field(default_factory=lambda: [DeliveryState.DELIVERED])
    failure_category: FailureCategory = FailureCategory.NONE
    seen: list[DeliveryAttempt] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.outcomes or not all(
            isinstance(state, DeliveryState) for state in self.outcomes
        ):
            raise AnalysisInputError("outcomes must be a nonempty list of DeliveryState")
        if not isinstance(self.failure_category, FailureCategory):
            raise AnalysisInputError("failure_category must be a FailureCategory")

    def deliver(self, attempt: DeliveryAttempt) -> DeliveryReceipt:
        if not isinstance(attempt, DeliveryAttempt):
            raise AnalysisInputError("FakeTransportSink.deliver requires a DeliveryAttempt")
        self.seen.append(attempt)
        index = min(attempt.attempt_number - 1, len(self.outcomes) - 1)
        state = self.outcomes[index]
        category = (
            self.failure_category if state is not DeliveryState.DELIVERED else FailureCategory.NONE
        )
        return _receipt(attempt, state, category)


class DeliveryRegistry:
    """In-process record of already-``DELIVERED`` delivery ids.

    Restart persistence is out of scope for Phase 24B; this registry is the
    deterministic at-most-once guard for repeated delivery calls within a
    process run.
    """

    def __init__(self) -> None:
        self._delivered: set[str] = set()

    def mark_delivered(self, delivery_id: str) -> None:
        if not isinstance(delivery_id, str) or not delivery_id.startswith("delivery:"):
            raise AnalysisInputError("delivery_id must carry the delivery: prefix")
        self._delivered.add(delivery_id)

    def is_delivered(self, delivery_id: str) -> bool:
        return delivery_id in self._delivered


def _receipt(
    attempt: DeliveryAttempt,
    state: DeliveryState,
    category: FailureCategory = FailureCategory.NONE,
) -> DeliveryReceipt:
    return DeliveryReceipt(
        delivery_id=attempt.delivery_id,
        message_id=attempt.message_id,
        signal_id=attempt.signal_id,
        destination_id=attempt.destination_id,
        attempt_number=attempt.attempt_number,
        state=state,
        failure_category=category,
    )


def deliver_message(
    message: RenderedSignalMessage,
    destination_id: str,
    sink: MessageSink | None,
    *,
    registry: DeliveryRegistry | None = None,
    max_attempts: int = 1,
) -> DeliveryReceipt:
    """Deliver one rendered message to one destination through a sink.

    Returns ``NOT_ATTEMPTED`` when no sink is configured. Reuses a single
    deterministic ``delivery_id`` across retries. ``UNKNOWN`` outcomes are not
    silently converted to success; ``DELIVERED`` outcomes are recorded in the
    optional registry so the same message is not re-sent.
    """
    if not isinstance(message, RenderedSignalMessage):
        raise AnalysisInputError("deliver_message requires a RenderedSignalMessage")
    if not isinstance(destination_id, str) or not destination_id.strip():
        raise AnalysisInputError("destination_id must be a nonempty string")
    if sink is None:
        message_id = message.message_id
        delivery_id = delivery_identity(message_id, destination_id)
        return DeliveryReceipt(
            delivery_id=delivery_id,
            message_id=message_id,
            signal_id=message.signal_id,
            destination_id=destination_id,
            attempt_number=1,
            state=DeliveryState.NOT_ATTEMPTED,
            failure_category=FailureCategory.CONFIG,
        )
    if type(max_attempts) is not int or max_attempts < 1:
        raise AnalysisInputError("max_attempts must be a positive integer")

    delivery_id = delivery_identity(message.message_id, destination_id)
    if registry is not None and registry.is_delivered(delivery_id):
        return DeliveryReceipt(
            delivery_id=delivery_id,
            message_id=message.message_id,
            signal_id=message.signal_id,
            destination_id=destination_id,
            attempt_number=1,
            state=DeliveryState.SKIPPED_DUPLICATE,
            failure_category=FailureCategory.NONE,
        )

    receipt: DeliveryReceipt | None = None
    chart_id = message.chart.artifact_id if message.chart is not None else None
    for attempt_number in range(1, max_attempts + 1):
        attempt = DeliveryAttempt(
            delivery_id=delivery_id,
            message_id=message.message_id,
            signal_id=message.signal_id,
            destination_id=destination_id,
            attempt_number=attempt_number,
            chart_artifact_id=chart_id,
        )
        receipt = sink.deliver(attempt)
        if receipt.state in (DeliveryState.DELIVERED, DeliveryState.SENT):
            if registry is not None:
                registry.mark_delivered(delivery_id)
            return receipt
        if receipt.state is DeliveryState.UNKNOWN:
            # Ambiguous: may or may not have delivered. Do not retry silently.
            return receipt
        if receipt.state is DeliveryState.NOT_ATTEMPTED:
            # A no-op/disabled sink stops the coordinator immediately.
            return receipt
        # FAILED: retry up to the attempt cap.
    if receipt is None:  # pragma: no cover - guarded by max_attempts >= 1
        raise AnalysisInputError("delivery produced no receipt")
    return receipt


def deliver_signal(
    frame: SignalSnapshot,
    destination_id: str,
    sink: MessageSink | None,
    *,
    attribution: SetupAttribution | None = None,
    drawing: DrawingModel | None = None,
    config: DeliveryConfig | None = None,
    registry: DeliveryRegistry | None = None,
    max_attempts: int = 1,
) -> DeliveryReceipt:
    """Project, render, and deliver one published BUY signal snapshot.

    ``frame`` must be a ``SignalSnapshot`` with status ``BUY_SIGNAL``; any other
    status is rejected here rather than silently broadcast as a buy. An optional
    chart is attached only when it matches the signal series; any chart failure
    is swallowed so the text message still delivers.
    """
    snapshot = message_core.require_buy_signal(frame)
    settings = config if config is not None else DeliveryConfig()
    message = SignalMessage.from_signal(snapshot, attribution)
    chart_attachment = _try_attach_chart(message, drawing)
    rendered = message_core.assemble_signal_message(
        message,
        chart=chart_attachment,
        config=settings,
    )
    return deliver_message(
        rendered,
        destination_id,
        sink,
        registry=registry,
        max_attempts=max_attempts,
    )


def _try_attach_chart(
    message: SignalMessage, drawing: DrawingModel | None
) -> ChartAttachment | None:
    """Best-effort chart attachment; never raises and never mutates the message."""
    if drawing is None:
        return None
    try:
        return chart.svg_attachment(
            drawing,
            signal_id=message.signal_id,
            symbol=message.symbol,
            timeframe=message.timeframe,
        )
    except AnalysisInputError:
        # A chart that does not belong to the signal, or that fails to render,
        # must not invalidate the text message.
        return None
