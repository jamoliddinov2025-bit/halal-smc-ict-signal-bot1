"""Phase 35F: the read-only operator snapshot over the frozen live seams.

This module derives one immutable, versioned picture of the *already running*
live deployment. It adds no scheduler, no persistence, no transport, no
monitoring state, and no second source of truth: every field is a projection of
a value an existing frozen component already owns and exposes.

    LifecycleState      ← declared operator intent for the running loop
    ConfigurationView   ← the Phase 35B canonical ``configuration_identity``
    CycleView           ← ``LivePollLoop.cycles`` / ``.consecutive_failures``
                           / ``.last_result`` (the frozen loop is the counter)
    CheckpointView      ← ``CycleReport.checkpoint_persisted`` of the most
                           recent cycle — a *view* of the Phase 35E outcome;
                           checkpoint format and ownership are untouched
    RetryAfterView      ← the one authorized accessor,
                           ``RetryAfterAwareLivePollLoop.retry_after_delay``
    OutboxView          ← aggregated over durable ``OutboxRecord`` values
    DeliveryView        ← the failure category of the most recent outbox record

Deliberately absent
-------------------

There is no GAP state, no RECOVERY state, no GAP retention, no recovery
classification, and no ``last_delivery_error``. Phase 35C already owns
continuity (``smcsignal.live.gap``); this module neither re-derives nor
retains it.

Delivery failure category
-------------------------

``latest_delivery_category`` reports the category of the *latest record*, not a
sticky historical failure state. Selection is deterministic — greatest
``updated_at``, then greatest ``delivery_id`` as tie-break — and
``FailureCategory.NONE`` is normalized to ``None``. A later successful record
therefore clears a previous failure category, because a frozen ``DELIVERED``
receipt always carries ``FailureCategory.NONE``.

Immutability and fail-closed validation
---------------------------------------

``OperatorSnapshot`` is frozen and slotted; every field is validated in
``__post_init__`` and violations are collected in a fixed field order, so the
same invalid value always produces the same message. Nullability is exact:
``cycles.last_cycle_success`` is ``None`` *only* before the first cycle, and
``checkpoint.last_persist_success`` is a genuine tri-state (``None`` = not yet
attempted, ``False`` = attempted and did not persist, ``True`` = persisted).

Importing this module performs no IO, no network call, no clock read, and no
persistence: ``generated_at`` is always supplied by the caller.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.models import FailureCategory
from smcsignal.delivery.outbox.models import TERMINAL_STATES, OutboxRecord, OutboxState
from smcsignal.live.config import LiveConfigurationError
from smcsignal.live.configuration_binding import LiveConfigurationBinding, configuration_identity
from smcsignal.live.poll_loop import LivePollLoop, PollOutcome

OPERATOR_SNAPSHOT_SCHEMA_VERSION = 1
OPERATOR_SNAPSHOT_METHODOLOGY = "live-operator-snapshot-v1"

#: Durable outbox lifecycle states that mean "work is still outstanding".
PENDING_OUTBOX_STATES: frozenset[OutboxState] = frozenset(
    {
        OutboxState.QUEUED,
        OutboxState.IN_FLIGHT,
        OutboxState.WAITING,
    }
)

#: Terminal outbox states that did not end in a confirmed delivery.
FAILED_OUTBOX_STATES: frozenset[OutboxState] = frozenset(TERMINAL_STATES) - {OutboxState.DELIVERED}


class OperatorSnapshotValidationError(LiveConfigurationError):
    """An operator snapshot failed schema, type, nullability, or invariant checks."""


class LifecycleState(StrEnum):
    """Declared operator intent for the running loop.

    Exactly three members: ``RUNNING`` (the loop is polling), ``STOPPED`` (the
    loop exited cleanly), and ``FAILED`` (the loop stopped on a fatal failure).
    There is no GAP member and no RECOVERY member — continuity and retry are
    owned by Phase 35C and are reported here only through the retained
    ``retry_after`` and ``cycles`` views.
    """

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


# -- validation helpers ---------------------------------------------------------


def _violation(field: str, detail: str) -> str:
    return f"{field}: {detail}"


def _rejection(*violations: str) -> OperatorSnapshotValidationError:
    """The one deterministic rejection message shape for an invalid snapshot."""

    return OperatorSnapshotValidationError(
        "invalid operator snapshot (" + "; ".join(violations) + ")"
    )


def _strict_bool(value: object, field: str, violations: list[str]) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        violations.append(_violation(field, "must be a boolean or None"))
        return None
    return value


def _count(value: object, field: str, violations: list[str]) -> int | None:
    if type(value) is not int:
        violations.append(_violation(field, "must be a nonnegative integer"))
        return None
    if value < 0:
        violations.append(_violation(field, "must be a nonnegative integer"))
        return None
    return value


def _instant(value: object, field: str, violations: list[str]) -> datetime | None:
    if not isinstance(value, datetime):
        violations.append(_violation(field, "must be a timezone-aware datetime"))
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        violations.append(_violation(field, "must be a timezone-aware datetime"))
        return None
    return value


def _require_instant(value: object, field: str) -> datetime:
    """Strict single-value form of :func:`_instant` for the derivation entry point."""

    violations: list[str] = []
    moment = _instant(value, field, violations)
    if moment is None:
        raise _rejection(*violations)
    return moment


def _identity_text(value: object, field: str, violations: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        violations.append(_violation(field, "must be a nonempty, trimmed string"))
        return None
    return value


# -- the projected views --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LifecycleView:
    """The declared lifecycle of the loop being observed."""

    state: LifecycleState


@dataclass(frozen=True, slots=True)
class ConfigurationView:
    """The Phase 35B canonical identity of the declared configuration.

    The identity is the frozen ``configuration_digest``; this module never
    invents a second canon and never trusts a stored digest blindly.
    """

    identity: str


@dataclass(frozen=True, slots=True)
class CycleView:
    """Loop-sourced cycle counts.

    ``completed_cycles`` is every cycle the loop executed; ``last_cycle_success``
    is ``None`` only before the first cycle, so a zero-failure start is never
    mistaken for observed success.
    """

    completed_cycles: int
    consecutive_failures: int
    last_cycle_success: bool | None


@dataclass(frozen=True, slots=True)
class CheckpointView:
    """Tri-state view of the most recent Phase 35E checkpoint persistence.

    ``None`` — no cycle has completed yet, so nothing was attempted.
    ``False`` — the last cycle ran and the checkpoint did not persist.
    ``True`` — the last cycle ran and the checkpoint persisted.
    """

    last_persist_success: bool | None


@dataclass(frozen=True, slots=True)
class RetryAfterView:
    """The server-directed delay the loop is currently honoring.

    ``active`` is exactly ``delay_seconds is not None``; reading it never
    mutates the holder, never reschedules, and never changes the backoff.
    """

    active: bool
    delay_seconds: float | None


@dataclass(frozen=True, slots=True)
class OutboxView:
    """Aggregated durable-outbox backlog.

    ``pending_count`` counts non-terminal records, ``failed_count`` counts
    terminal records that were not delivered, and ``healthy`` is the store's own
    integrity (no quarantined record documents and no meta bootstrap failure) —
    a separate axis from backlog, so the two degraded conditions stay
    independently observable.
    """

    pending_count: int
    failed_count: int
    healthy: bool


@dataclass(frozen=True, slots=True)
class DeliveryView:
    """The failure category of the most recent durable outbox record.

    ``None`` means "no record, or the latest record reports no failure" — it is
    never ``FailureCategory.NONE`` and it is not sticky history.
    """

    last_failure_category: FailureCategory | None


@dataclass(frozen=True, slots=True)
class OperatorSnapshot:
    """One immutable, versioned picture of the live deployment.

    Every field is validated on construction; an invalid snapshot cannot exist.
    The snapshot holds no clock, no store handle, no transport, and no thread.
    """

    schema_version: int
    generated_at: datetime
    lifecycle: LifecycleView
    configuration: ConfigurationView
    cycles: CycleView
    checkpoint: CheckpointView
    retry_after: RetryAfterView
    outbox: OutboxView
    delivery: DeliveryView
    methodology: str = OPERATOR_SNAPSHOT_METHODOLOGY

    def __post_init__(self) -> None:
        violations: list[str] = []
        self._collect_violations(violations)
        if violations:
            raise _rejection(*violations)

    # -- invariant collection -------------------------------------------------

    def _collect_violations(self, violations: list[str]) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != OPERATOR_SNAPSHOT_SCHEMA_VERSION
        ):
            violations.append(
                _violation(
                    "schema_version",
                    f"must be exactly {OPERATOR_SNAPSHOT_SCHEMA_VERSION}",
                )
            )
        if self.methodology != OPERATOR_SNAPSHOT_METHODOLOGY:
            violations.append(
                _violation("methodology", f"must be {OPERATOR_SNAPSHOT_METHODOLOGY!r}")
            )
        _instant(self.generated_at, "generated_at", violations)

        if not isinstance(self.lifecycle, LifecycleView):
            violations.append(_violation("lifecycle", "must be a LifecycleView"))
        elif not isinstance(self.lifecycle.state, LifecycleState):
            violations.append(_violation("lifecycle.state", "must be a LifecycleState"))

        if not isinstance(self.configuration, ConfigurationView):
            violations.append(_violation("configuration", "must be a ConfigurationView"))
        else:
            _identity_text(self.configuration.identity, "configuration.identity", violations)

        if not isinstance(self.cycles, CycleView):
            violations.append(_violation("cycles", "must be a CycleView"))
        else:
            self._collect_cycle_violations(violations)

        if not isinstance(self.checkpoint, CheckpointView):
            violations.append(_violation("checkpoint", "must be a CheckpointView"))
        else:
            _strict_bool(
                self.checkpoint.last_persist_success,
                "checkpoint.last_persist_success",
                violations,
            )

        if not isinstance(self.retry_after, RetryAfterView):
            violations.append(_violation("retry_after", "must be a RetryAfterView"))
        else:
            self._collect_retry_after_violations(violations)

        if not isinstance(self.outbox, OutboxView):
            violations.append(_violation("outbox", "must be an OutboxView"))
        else:
            self._collect_outbox_violations(violations)

        if not isinstance(self.delivery, DeliveryView):
            violations.append(_violation("delivery", "must be a DeliveryView"))
        else:
            category = self.delivery.last_failure_category
            if category is not None:
                if not isinstance(category, FailureCategory):
                    violations.append(
                        _violation(
                            "delivery.last_failure_category",
                            "must be a FailureCategory or None",
                        )
                    )
                elif category is FailureCategory.NONE:
                    violations.append(
                        _violation(
                            "delivery.last_failure_category",
                            "FailureCategory.NONE must be represented as None",
                        )
                    )

    def _collect_cycle_violations(self, violations: list[str]) -> None:
        completed = _count(self.cycles.completed_cycles, "cycles.completed_cycles", violations)
        failures = _count(
            self.cycles.consecutive_failures, "cycles.consecutive_failures", violations
        )
        raw_last = self.cycles.last_cycle_success
        if raw_last is not None and type(raw_last) is not bool:
            violations.append(_violation("cycles.last_cycle_success", "must be a boolean or None"))
            # The nullability invariants below are only meaningful over a
            # well-typed value; the count ordering still applies.
            raw_last = None
            last_well_typed = False
        else:
            last_well_typed = True
        if completed is None or failures is None:
            return
        if failures > completed:
            violations.append(
                _violation(
                    "cycles.consecutive_failures",
                    "cannot exceed cycles.completed_cycles",
                )
            )
            return
        if not last_well_typed:
            return
        if completed == 0:
            if raw_last is not None:
                violations.append(
                    _violation(
                        "cycles.last_cycle_success",
                        "must be None before the first completed cycle",
                    )
                )
            return
        if raw_last is None:
            violations.append(
                _violation(
                    "cycles.last_cycle_success",
                    "must be a boolean once at least one cycle completed",
                )
            )
            return
        if failures > 0 and raw_last is not False:
            violations.append(
                _violation(
                    "cycles.last_cycle_success",
                    "must be False while cycles.consecutive_failures is positive",
                )
            )
        if raw_last is True and failures != 0:
            violations.append(
                _violation(
                    "cycles.consecutive_failures",
                    "must be zero when the last cycle succeeded",
                )
            )

    def _collect_retry_after_violations(self, violations: list[str]) -> None:
        active = self.retry_after.active
        if type(active) is not bool:
            violations.append(_violation("retry_after.active", "must be a boolean"))
            return
        delay = self.retry_after.delay_seconds
        if delay is None:
            if active:
                violations.append(
                    _violation(
                        "retry_after.delay_seconds",
                        "must be a finite number of seconds when active is True",
                    )
                )
            return
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(delay)
        ):
            violations.append(
                _violation("retry_after.delay_seconds", "must be a finite number of seconds")
            )
            return
        if delay < 0:
            violations.append(_violation("retry_after.delay_seconds", "must not be negative"))
            return
        if not active:
            violations.append(
                _violation(
                    "retry_after.active",
                    "must be True when delay_seconds is not None",
                )
            )

    def _collect_outbox_violations(self, violations: list[str]) -> None:
        _count(self.outbox.pending_count, "outbox.pending_count", violations)
        _count(self.outbox.failed_count, "outbox.failed_count", violations)
        if type(self.outbox.healthy) is not bool:
            violations.append(_violation("outbox.healthy", "must be a boolean"))


# -- derivations ----------------------------------------------------------------


def configuration_view(
    source: BacktestConfiguration | LiveConfigurationBinding | str,
) -> ConfigurationView:
    """Project a declared configuration onto its canonical Phase 35B identity."""

    if isinstance(source, LiveConfigurationBinding):
        identity = source.identity
    elif isinstance(source, BacktestConfiguration):
        identity = configuration_identity(source)
    elif isinstance(source, str):
        identity = source
    else:
        raise AnalysisInputError(
            "configuration_view requires a BacktestConfiguration, a "
            "LiveConfigurationBinding, or a canonical identity string"
        )
    return ConfigurationView(identity=identity)


def cycle_view(loop: LivePollLoop) -> CycleView:
    """Read cycle counts straight off the frozen loop — the loop stays the counter."""

    if not isinstance(loop, LivePollLoop):
        raise AnalysisInputError("cycle_view requires a LivePollLoop")
    last_result = loop.last_result
    last_cycle_success: bool | None = None
    if last_result is not None:
        last_cycle_success = last_result.outcome is PollOutcome.SUCCESS
    return CycleView(
        completed_cycles=loop.cycles,
        consecutive_failures=loop.consecutive_failures,
        last_cycle_success=last_cycle_success,
    )


def checkpoint_view(loop: LivePollLoop) -> CheckpointView:
    """Project the most recent ``CycleReport.checkpoint_persisted`` tri-state.

    The checkpoint format, key, and store ownership stay in Phase 35E; this
    reads only what the frozen report already publishes. A cycle that failed
    before reaching checkpoint persistence publishes no report and therefore
    carries no new observation, so the most recent report wins. ``None`` is
    strictly "no cycle has ever produced a report", never "it failed".
    """

    if not isinstance(loop, LivePollLoop):
        raise AnalysisInputError("checkpoint_view requires a LivePollLoop")
    for result in reversed(loop.history):
        if result.report is not None:
            return CheckpointView(last_persist_success=bool(result.report.checkpoint_persisted))
    return CheckpointView(last_persist_success=None)


def retry_after_view(loop: object) -> RetryAfterView:
    """Read the single authorized Retry-After accessor without mutating it.

    Only the public ``retry_after_delay`` property is consulted; a loop that does
    not carry the Phase 35C Retry-After support reports an inactive view.
    """

    delay = getattr(loop, "retry_after_delay", None)
    if delay is None:
        return RetryAfterView(active=False, delay_seconds=None)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay):
        raise AnalysisInputError("retry_after_delay must be a finite number of seconds or None")
    if delay < 0:
        raise AnalysisInputError("retry_after_delay must not be negative")
    return RetryAfterView(active=True, delay_seconds=float(delay))


def _records_of(records: Sequence[OutboxRecord]) -> tuple[OutboxRecord, ...]:
    """Validate one outbox record sequence deterministically (fail closed)."""

    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise AnalysisInputError("a sequence of OutboxRecord values is required")
    for index, record in enumerate(records):
        if not isinstance(record, OutboxRecord):
            raise AnalysisInputError(f"outbox record at index {index} must be an OutboxRecord")
    return tuple(records)


def outbox_view(
    records: Sequence[OutboxRecord],
    *,
    quarantine_count: int = 0,
    meta_failure: str | None = None,
) -> OutboxView:
    """Aggregate durable outbox records into backlog and store-integrity counts."""

    found = _records_of(records)
    if type(quarantine_count) is not int or quarantine_count < 0:
        raise AnalysisInputError("quarantine_count must be a nonnegative integer")
    if meta_failure is not None and not isinstance(meta_failure, str):
        raise AnalysisInputError("meta_failure must be a string or None")
    pending = sum(1 for record in found if record.state in PENDING_OUTBOX_STATES)
    failed = sum(1 for record in found if record.state in FAILED_OUTBOX_STATES)
    healthy = quarantine_count == 0 and meta_failure is None
    return OutboxView(pending_count=pending, failed_count=failed, healthy=healthy)


def latest_outbox_record(records: Sequence[OutboxRecord]) -> OutboxRecord | None:
    """The most recent durable record: greatest ``updated_at``, then ``delivery_id``."""

    found = _records_of(records)
    if not found:
        return None
    return max(found, key=lambda record: (record.updated_at, record.delivery_id))


def latest_delivery_category(records: Sequence[OutboxRecord]) -> FailureCategory | None:
    """Category of the latest record, with ``NONE`` normalized to ``None``.

    This is the category of the *latest attempt/record*, never sticky historical
    failure state: a later successful record reports ``None``.
    """

    record = latest_outbox_record(records)
    if record is None:
        return None
    category = record.last_failure_category
    if category is None or category is FailureCategory.NONE:
        return None
    return category


def delivery_view(records: Sequence[OutboxRecord]) -> DeliveryView:
    """Project the durable outbox onto the latest delivery failure category."""

    return DeliveryView(last_failure_category=latest_delivery_category(records))


def build_operator_snapshot(
    *,
    lifecycle_state: LifecycleState,
    configuration: BacktestConfiguration | LiveConfigurationBinding | str,
    loop: LivePollLoop,
    generated_at: datetime,
    outbox_records: Sequence[OutboxRecord] = (),
    outbox_quarantine_count: int = 0,
    outbox_meta_failure: str | None = None,
    checkpoint_last_persist_success: bool | None = None,
    checkpoint_from_loop: bool = True,
) -> OperatorSnapshot:
    """Compose one validated snapshot from the frozen seams.

    ``generated_at`` is caller-supplied (no clock is read here) and must not
    predate the durable outbox evidence it summarizes — a snapshot cannot report
    records that did not yet exist when it was generated. The checkpoint view
    defaults to the loop's most recent report; pass
    ``checkpoint_from_loop=False`` with an explicit tri-state when the caller
    owns that observation.
    """

    if not isinstance(lifecycle_state, LifecycleState):
        raise AnalysisInputError("build_operator_snapshot requires a LifecycleState")
    if type(checkpoint_from_loop) is not bool:
        raise AnalysisInputError("checkpoint_from_loop must be a boolean")
    moment = _require_instant(generated_at, "generated_at")
    records = _records_of(outbox_records)
    if records:
        evidence = max(record.updated_at for record in records)
        if evidence > moment:
            raise _rejection(
                _violation(
                    "generated_at",
                    "must not predate the durable outbox evidence it summarizes "
                    f"(latest record updated_at is {evidence.isoformat()})",
                )
            )
    if checkpoint_from_loop:
        if checkpoint_last_persist_success is not None:
            raise AnalysisInputError(
                "checkpoint_last_persist_success requires checkpoint_from_loop=False"
            )
        checkpoint = checkpoint_view(loop)
    else:
        if (
            checkpoint_last_persist_success is not None
            and type(checkpoint_last_persist_success) is not bool
        ):
            raise AnalysisInputError("checkpoint_last_persist_success must be a boolean or None")
        checkpoint = CheckpointView(last_persist_success=checkpoint_last_persist_success)

    return OperatorSnapshot(
        schema_version=OPERATOR_SNAPSHOT_SCHEMA_VERSION,
        generated_at=moment,
        lifecycle=LifecycleView(state=lifecycle_state),
        configuration=configuration_view(configuration),
        cycles=cycle_view(loop),
        checkpoint=checkpoint,
        retry_after=retry_after_view(loop),
        outbox=outbox_view(
            records,
            quarantine_count=outbox_quarantine_count,
            meta_failure=outbox_meta_failure,
        ),
        delivery=delivery_view(records),
    )


__all__ = [
    "FAILED_OUTBOX_STATES",
    "OPERATOR_SNAPSHOT_METHODOLOGY",
    "OPERATOR_SNAPSHOT_SCHEMA_VERSION",
    "PENDING_OUTBOX_STATES",
    "CheckpointView",
    "ConfigurationView",
    "CycleView",
    "DeliveryView",
    "LifecycleState",
    "LifecycleView",
    "OperatorSnapshot",
    "OperatorSnapshotValidationError",
    "OutboxView",
    "RetryAfterView",
    "build_operator_snapshot",
    "checkpoint_view",
    "configuration_view",
    "cycle_view",
    "delivery_view",
    "latest_delivery_category",
    "latest_outbox_record",
    "outbox_view",
    "retry_after_view",
]
