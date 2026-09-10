"""Phase 24C deterministic delivery audit records and safe redaction.

Audit records are produced by the delivery orchestrator from immutable data only.
They never depend on the wall clock, randomness, or the environment, and they
never carry secret material (no bot token, no chat id, no api key, no endpoint).
``redact`` lets any identity-like value be stored in an audit record without
exposing its full value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import digest

from .models import DeliveryState, FailureCategory

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from .orchestrator import DeliveryOutcome

_METHODOLOGY = "delivery-audit-v1"
_REDACTED_PREFIX = "<redacted>"


def redact(value: str, *, keep: int = 4) -> str:
    """Deterministically redact an identity-like value.

    Only the final ``keep`` characters are retained (when available), prefixed
    with a fixed marker so the output length is stable regardless of input
    length. This is a lossy, one-way transformation intended for audit display;
    it must never be used to reconstruct the original value.
    """
    if not isinstance(value, str) or not value.strip():
        raise AnalysisInputError("redact requires a nonempty string")
    if type(keep) is not int or keep < 0:
        raise AnalysisInputError("keep must be a nonnegative integer")
    if keep == 0 or len(value) <= keep:
        return _REDACTED_PREFIX
    return f"{_REDACTED_PREFIX}:{value[-keep:]}"


@dataclass(frozen=True, slots=True)
class DeliveryAuditRecord:
    """Deterministic, secret-free audit record for one delivery outcome.

    All fields are content-derived from the immutable outcome. There are no
    timestamps (they would break determinism) and no secret material. The
    destination is stored only in redacted form.
    """

    delivery_id: str
    message_id: str
    signal_id: str
    destination_redacted: str
    state: DeliveryState
    failure_category: FailureCategory
    attempt_number: int
    chart_artifact_id: str | None
    message_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.delivery_id, str) or not self.delivery_id.startswith("delivery:"):
            raise AnalysisInputError("delivery_id must carry the delivery: prefix")
        if not isinstance(self.message_id, str) or not self.message_id.startswith("message:"):
            raise AnalysisInputError("message_id must carry the message: prefix")
        if not isinstance(self.signal_id, str) or not self.signal_id.strip():
            raise AnalysisInputError("signal_id must be a nonempty string")
        if not isinstance(self.destination_redacted, str) or not self.destination_redacted.strip():
            raise AnalysisInputError("destination_redacted must be a nonempty string")
        if not isinstance(self.state, DeliveryState):
            raise AnalysisInputError("state must be a DeliveryState")
        if not isinstance(self.failure_category, FailureCategory):
            raise AnalysisInputError("failure_category must be a FailureCategory")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise AnalysisInputError("attempt_number must be a positive integer")
        if self.chart_artifact_id is not None and (
            not isinstance(self.chart_artifact_id, str)
            or not self.chart_artifact_id.startswith("chart:")
        ):
            raise AnalysisInputError("chart_artifact_id must carry the chart: prefix")
        if not isinstance(self.message_digest, str) or not self.message_digest:
            raise AnalysisInputError("message_digest must be a nonempty string")


def _message_digest(message_id: str, caption: str) -> str:
    return digest(
        {
            "methodology": _METHODOLOGY,
            "kind": "rendered-caption",
            "message_id": message_id,
            "caption": caption,
        }
    )


def audit_record_for(outcome: DeliveryOutcome) -> DeliveryAuditRecord:
    """Build a deterministic audit record from a delivery outcome envelope.

    The outcome must be a ``DeliveryOutcome`` (see ``orchestrator.py``). Only
    immutable, redacted data is copied into the record.
    """
    from .orchestrator import DeliveryOutcome

    if not isinstance(outcome, DeliveryOutcome):
        raise AnalysisInputError("audit_record_for requires a DeliveryOutcome")
    return DeliveryAuditRecord(
        delivery_id=outcome.delivery_id,
        message_id=outcome.message_id,
        signal_id=outcome.signal_id,
        destination_redacted=redact(outcome.destination_id),
        state=outcome.state,
        failure_category=outcome.failure_category,
        attempt_number=outcome.attempt_number,
        chart_artifact_id=outcome.chart_artifact_id,
        message_digest=_message_digest(outcome.message_id, outcome.rendered.caption),
    )
