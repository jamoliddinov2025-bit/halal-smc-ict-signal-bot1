"""Immutable Phase 24 delivery records and transport states.

These records describe what is *ready to be delivered* and what happened to a
delivery attempt. They are deliberately transport-independent: the presentation
layer produces a rendered message plus an optional chart artifact, and a
``MessageSink`` (see ``sink.py``) turns a delivery attempt into a receipt. No
record here can mutate a signal or change any Phase 1-23 decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import digest

METHODOLOGY_VERSION = "delivery-v1"


class DeliveryState(StrEnum):
    """Transport state of one message to one destination.

    ``NOT_ATTEMPTED`` precedes any call to a sink; ``SENT`` means a sink accepted
    the send; ``DELIVERED``/``UNKNOWN``/``FAILED`` describe an outcome the sink
    reports. No exactly-once guarantee is claimed; network/transport semantics
    are deferred to the concrete transport implementation (Phase 24D).
    """

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


class FailureCategory(StrEnum):
    """Deterministic category of a failed or unresolved delivery."""

    NONE = "none"
    BUILD = "build"
    CHART = "chart"
    CONFIG = "config"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    UNKNOWN = "unknown"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class ChartAttachment:
    """One deterministic chart artifact bound to a signal identity.

    ``content`` is an SVG serialization produced by the existing Phase 19e
    renderer (``render_svg``). Raster (PNG) conversion is deliberately deferred;
    this record is a pure, serializable representation of an already-created
    ``DrawingModel``.
    """

    signal_id: str
    drawing_id: str
    mime_type: str
    filename: str
    content: str
    artifact_id: str

    def __post_init__(self) -> None:
        _text(self.signal_id, "signal_id")
        _text(self.drawing_id, "drawing_id")
        if not self.drawing_id.startswith("drawing:"):
            raise AnalysisInputError("drawing_id must carry the drawing: prefix")
        _text(self.mime_type, "mime_type")
        _text(self.filename, "filename")
        if not isinstance(self.content, str) or not self.content:
            raise AnalysisInputError("content must be a nonempty string")
        _text(self.artifact_id, "artifact_id")
        expected = chart_artifact_id(self.signal_id, self.drawing_id, self.mime_type)
        if self.artifact_id != expected:
            raise AnalysisInputError("artifact_id must match the deterministic identity")


def chart_artifact_id(signal_id: str, drawing_id: str, mime_type: str) -> str:
    """Deterministic chart-artifact identity from stable inputs."""
    _text(signal_id, "signal_id")
    _text(drawing_id, "drawing_id")
    _text(mime_type, "mime_type")
    return "chart:" + digest(
        {
            "methodology": METHODOLOGY_VERSION,
            "kind": "chart-artifact",
            "signal_id": signal_id,
            "drawing_id": drawing_id,
            "mime_type": mime_type,
        }
    )


@dataclass(frozen=True, slots=True)
class RenderedSignalMessage:
    """A fully rendered signal message, ready for any transport.

    ``signal_id`` preserves the immutable upstream identity. ``caption`` is the
    deterministic, HTML-escaped presentation text. ``parts`` holds the caption
    split at safe boundaries for transports that impose a per-message limit.
    ``chart`` is an optional already-prepared artifact.
    """

    signal_id: str
    message_id: str
    caption: str
    parts: tuple[str, ...]
    chart: ChartAttachment | None = None

    def __post_init__(self) -> None:
        _text(self.signal_id, "signal_id")
        _text(self.message_id, "message_id")
        if not self.message_id.startswith("message:"):
            raise AnalysisInputError("message_id must carry the message: prefix")
        if not isinstance(self.caption, str) or not self.caption.strip():
            raise AnalysisInputError("caption must be a nonempty string")
        if (
            not isinstance(self.parts, tuple)
            or not self.parts
            or not all(isinstance(part, str) and part.strip() for part in self.parts)
        ):
            raise AnalysisInputError("parts must be a nonempty tuple of nonempty strings")
        if self.chart is not None and not isinstance(self.chart, ChartAttachment):
            raise AnalysisInputError("chart must be a ChartAttachment or None")
        if self.chart is not None and self.chart.signal_id != self.signal_id:
            raise AnalysisInputError("chart must reference this exact signal")


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """One attempt to deliver a message to a destination through a sink."""

    delivery_id: str
    message_id: str
    signal_id: str
    destination_id: str
    attempt_number: int
    chart_artifact_id: str | None = None

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
        if self.chart_artifact_id is not None:
            _text(self.chart_artifact_id, "chart_artifact_id")
            if not self.chart_artifact_id.startswith("chart:"):
                raise AnalysisInputError("chart_artifact_id must carry the chart: prefix")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """Outcome of one delivery attempt produced by a sink or coordinator."""

    delivery_id: str
    message_id: str
    signal_id: str
    destination_id: str
    attempt_number: int
    state: DeliveryState
    failure_category: FailureCategory = FailureCategory.NONE
    attempted_at: datetime | None = None

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
        if not isinstance(self.state, DeliveryState):
            raise AnalysisInputError("state must be a DeliveryState")
        if not isinstance(self.failure_category, FailureCategory):
            raise AnalysisInputError("failure_category must be a FailureCategory")
        if self.attempted_at is not None and not isinstance(self.attempted_at, datetime):
            raise AnalysisInputError("attempted_at must be a datetime or None")
