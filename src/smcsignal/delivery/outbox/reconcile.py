"""Phase 35D: deterministic startup reconciliation for the durable outbox.

Reconciliation closes the crash window between durable market state and outbox
intent: a process that died after the candle window/ledger were persisted but
before an intent record was written leaves the signal undeliverable through
the normal cycle path (the cursor never reprocesses a persisted candle). At
startup the durable window is re-warmed into exactly the frames the frozen
chain would have produced, and every BUY frame whose candle is not older than
the store's activation marker is compared against the durable outbox records:

    expected delivery ids (window regeneration, frozen identities)
      − durable records that already exist
      = missing intents → recreated as QUEUED with a deterministically
        re-rendered payload (byte-identical to the first rendering)

Rules locked by the design:

- the durable window regeneration is authoritative for *which* signals must
  have been delivered; the durable outbox store is authoritative for *which*
  deliveries already reached any lifecycle state;
- existing records are never re-enqueued — terminal records are terminal
  across restarts, and non-terminal records continue through the drain;
- candles older than ``activated_at`` are excluded, so the first-ever outbox
  startup never re-broadcasts historical warm-up signals;
- an intent whose previous record was quarantined is recreated with
  ``ambiguous_attempt_seen`` set, because its prior attempt history is
  unknowable;
- processing order is the frame order (chronological); the function is pure
  with respect to its inputs and safe to re-run after any interruption.

Projection reuses the frozen coordinator in its disabled (render-only) mode:
identical identities and identical rendered bytes, no second renderer.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus
from smcsignal.delivery.identity import delivery_identity, message_identity
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.outbox.models import OutboxConfig, ensure_utc, new_queued_record
from smcsignal.delivery.outbox.store import FileOutboxStore
from smcsignal.delivery.transport import TransportPayload, transport_payload_from_outcome


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """One deterministic reconciliation pass summary."""

    considered: int
    created: tuple[str, ...]
    skipped_existing: int
    skipped_pre_activation: int

    @property
    def created_count(self) -> int:
        return len(self.created)


def expected_delivery_id(frame: SignalSnapshot, destination_id: str) -> str:
    """The frozen deterministic delivery identity of one BUY frame."""
    if not isinstance(frame, SignalSnapshot):
        raise AnalysisInputError("expected_delivery_id requires a SignalSnapshot")
    if frame.status is not SignalStatus.BUY_SIGNAL:
        raise AnalysisInputError("only BUY_SIGNAL frames carry a delivery intent")
    return delivery_identity(message_identity(frame.signal_id), destination_id)


def reconcile_missing(
    frames: Sequence[SignalSnapshot],
    destination_id: str,
    *,
    activated_at: datetime,
    store: FileOutboxStore,
    config: OutboxConfig | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ReconciliationReport:
    """Recreate missing durable intents for regenerated BUY frames.

    ``frames`` is the deterministic warm-up frame sequence of the durable
    window. Non-BUY frames are ignored. BUY frames whose candle opened before
    ``activated_at`` predate the outbox and are excluded. For every remaining
    frame whose ``delivery_id`` has no durable record, one ``QUEUED`` intent is
    written with the frozen projection of that exact frame. Records that
    already exist — terminal or not — are left untouched. Interrupting this
    function at any point is safe: the next startup recomputes the same
    expected set and completes the remainder.
    """
    if not isinstance(frames, Sequence) or not all(
        isinstance(frame, SignalSnapshot) for frame in frames
    ):
        raise AnalysisInputError("reconcile_missing requires a sequence of SignalSnapshot")
    if not isinstance(destination_id, str) or not destination_id.strip():
        raise AnalysisInputError("destination_id must be a nonempty string")
    if not isinstance(store, FileOutboxStore):
        raise AnalysisInputError("reconcile_missing requires a FileOutboxStore")
    resolved_config = config if config is not None else OutboxConfig()
    resolved_clock = clock if clock is not None else _default_clock
    activation = ensure_utc(activated_at)
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=False))
    quarantined = store.quarantined_ids()

    considered = 0
    skipped_pre_activation = 0
    skipped_existing = 0
    created: list[str] = []
    for frame in frames:
        if frame.status is not SignalStatus.BUY_SIGNAL:
            continue
        considered += 1
        candle_opened_at = ensure_utc(frame.candidate.signal.candle_opened_at)
        if candle_opened_at < activation:
            skipped_pre_activation += 1
            continue
        delivery_id = expected_delivery_id(frame, destination_id)
        if store.load_record(delivery_id) is not None:
            skipped_existing += 1
            continue
        outcome = coordinator.deliver(frame, destination_id)
        if outcome.delivery_id != delivery_id:
            raise AnalysisInputError(
                "reconciliation identity diverged from the frozen delivery identity"
            )
        payload: TransportPayload = transport_payload_from_outcome(outcome)
        record = new_queued_record(
            payload,
            now=resolved_clock(),
            max_attempts=resolved_config.max_attempts,
            ambiguous_attempt_seen=delivery_id in quarantined,
        )
        store.save_record(record)
        created.append(delivery_id)
    return ReconciliationReport(
        considered=considered,
        created=tuple(created),
        skipped_existing=skipped_existing,
        skipped_pre_activation=skipped_pre_activation,
    )
