"""Phase 24B public API: pure, offline, transport-independent signal delivery.

This layer formats already-published immutable ``SignalSnapshot`` records into
deterministic, HTML-escaped messages and binds an optional already-created
``DrawingModel`` chart artifact. It never connects to a network, never generates,
alters, or vetoes a signal, and never changes any Phase 1-23 decision. A concrete
remote messaging transport is deferred to a later phase and is never imported
here. The approved design is ``docs/phase24a-*-design.md``.
"""

from smcsignal.delivery.audit import DeliveryAuditRecord, audit_record_for, redact
from smcsignal.delivery.chart import SVG_MIME, svg_attachment
from smcsignal.delivery.config import (
    DEFAULT_MAX_CAPTION_LENGTH,
    DeliveryConfig,
    load_delivery_config,
)
from smcsignal.delivery.formatting import (
    escape_html,
    format_decimal,
    format_timestamp,
    split_caption,
)
from smcsignal.delivery.identity import delivery_identity, message_identity
from smcsignal.delivery.message import (
    SignalMessage,
    assemble_signal_message,
    prepare_signal_message,
    render_message,
    require_buy_signal,
)
from smcsignal.delivery.models import (
    ChartAttachment,
    DeliveryAttempt,
    DeliveryReceipt,
    DeliveryState,
    FailureCategory,
    RenderedSignalMessage,
    chart_artifact_id,
)
from smcsignal.delivery.orchestrator import (
    DeliveryBatchResult,
    DeliveryCoordinator,
    DeliveryOutcome,
    OrchestrationConfig,
    SignalDeliveryContext,
    load_orchestration_config,
)
from smcsignal.delivery.sink import (
    DeliveryRegistry,
    FakeTransportSink,
    MessageSink,
    NullSink,
    deliver_message,
    deliver_signal,
)

__all__ = [
    "ChartAttachment",
    "DEFAULT_MAX_CAPTION_LENGTH",
    "DeliveryAuditRecord",
    "DeliveryBatchResult",
    "DeliveryCoordinator",
    "DeliveryOutcome",
    "DeliveryAttempt",
    "DeliveryConfig",
    "DeliveryReceipt",
    "DeliveryRegistry",
    "DeliveryState",
    "FakeTransportSink",
    "FailureCategory",
    "MessageSink",
    "NullSink",
    "RenderedSignalMessage",
    "SVG_MIME",
    "SignalMessage",
    "assemble_signal_message",
    "chart_artifact_id",
    "deliver_message",
    "deliver_signal",
    "delivery_identity",
    "escape_html",
    "format_decimal",
    "format_timestamp",
    "load_delivery_config",
    "OrchestrationConfig",
    "SignalDeliveryContext",
    "audit_record_for",
    "load_orchestration_config",
    "message_identity",
    "prepare_signal_message",
    "redact",
    "render_message",
    "require_buy_signal",
    "split_caption",
    "svg_attachment",
]
