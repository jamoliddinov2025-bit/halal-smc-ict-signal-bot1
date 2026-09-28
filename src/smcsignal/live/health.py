"""Phase 35F: deterministic health classification of one operator snapshot.

This module is a *pure derivation*. It holds no mutable state, no clock, no
store, no thread, and no cache: the only input is an
:class:`~smcsignal.live.operator.OperatorSnapshot` and the only output is one of
exactly five states. It is therefore never a second source of truth — every
condition it evaluates is a field the snapshot already derived from a frozen
component, and the monitoring package (``smcsignal.monitoring``) is neither
imported nor consulted.

The vocabulary
--------------

Exactly five states, and no others:

    FAILED      the lifecycle itself failed
    RECOVERING  the loop is working through consecutive failures or is
                honoring a server-directed Retry-After delay
    DEGRADED    the loop is fine but a supporting subsystem is not
    UNKNOWN     required health information is not established, so health
                cannot be truthfully affirmed
    HEALTHY     every required fact is present and affirmative

There is no GAP state and no RECOVERY state; continuity and retry remain owned
by Phase 35C and surface here only through ``cycles`` and ``retry_after``.

First-match precedence
----------------------

Classification is a strict, deterministic first-match chain:

    FAILED → RECOVERING → DEGRADED → UNKNOWN → HEALTHY

The chain is total: a valid snapshot always resolves to exactly one state, and
the terminal ``UNKNOWN`` is fail-closed, so an unforeseen combination can never
be reported as ``HEALTHY``.

Two deliberate non-optimizations
--------------------------------

* A clean ``STOPPED`` lifecycle never becomes ``HEALTHY``. ``HEALTHY`` requires
  ``lifecycle.state is RUNNING``; a stopped loop that has no FAILED,
  RECOVERING, or DEGRADED condition and therefore insufficient information for
  ``HEALTHY`` is reported as ``UNKNOWN``.
* The pre-first-cycle state (``completed_cycles == 0`` with
  ``last_cycle_success is None``) is ``UNKNOWN``, never ``HEALTHY``. Zero
  failures is not observed success.
"""

from __future__ import annotations

from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.live.operator import LifecycleState, OperatorSnapshot

#: The locked evaluation order. Classification is first-match over this tuple.
HEALTH_PRECEDENCE: tuple[str, ...] = (
    "FAILED",
    "RECOVERING",
    "DEGRADED",
    "UNKNOWN",
    "HEALTHY",
)

# -- stable condition codes ------------------------------------------------------
#
# Every code below is a constant identifier (no interpolation), so the reason
# tuples are fully deterministic and directly assertable.

FAILED_LIFECYCLE = "lifecycle_state_failed"

CONSECUTIVE_FAILURES_PRESENT = "consecutive_failures_present"
RETRY_AFTER_ACTIVE = "retry_after_active"

OUTBOX_PENDING = "outbox_pending"
OUTBOX_FAILED = "outbox_failed"
OUTBOX_UNHEALTHY = "outbox_unhealthy"
CHECKPOINT_PERSIST_FAILED = "checkpoint_persist_failed"
DELIVERY_FAILURE_PRESENT = "delivery_failure_present"

NO_COMPLETED_CYCLES = "no_completed_cycles"
LAST_CYCLE_OUTCOME_UNESTABLISHED = "last_cycle_outcome_unestablished"
LIFECYCLE_NOT_RUNNING = "lifecycle_not_running"
LAST_CYCLE_UNSUCCESSFUL = "last_cycle_unsuccessful"


class OperatorHealthState(StrEnum):
    """The five-state live health vocabulary.

    Named distinctly from ``smcsignal.monitoring.models.HealthState`` — a
    different, stateful four-member rollup vocabulary (``FAILING``, not
    ``FAILED``; no ``RECOVERING``) owned by Phase 25A. Phase 35F derives its own
    closed set here and never reads monitoring state.
    """

    FAILED = "FAILED"
    RECOVERING = "RECOVERING"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"


def _require_snapshot(snapshot: object) -> OperatorSnapshot:
    if not isinstance(snapshot, OperatorSnapshot):
        raise AnalysisInputError("an OperatorSnapshot is required")
    return snapshot


# -- the validated rules ---------------------------------------------------------


def failed_conditions(snapshot: OperatorSnapshot) -> tuple[str, ...]:
    """``FAILED``: the lifecycle state is ``FAILED``."""

    _require_snapshot(snapshot)
    if snapshot.lifecycle.state is LifecycleState.FAILED:
        return (FAILED_LIFECYCLE,)
    return ()


def recovering_conditions(snapshot: OperatorSnapshot) -> tuple[str, ...]:
    """``RECOVERING``: not ``FAILED``, and consecutive failures or Retry-After.

    ``lifecycle.state != FAILED`` **and** (``cycles.consecutive_failures > 0``
    **or** ``retry_after.active``).
    """

    _require_snapshot(snapshot)
    if snapshot.lifecycle.state is LifecycleState.FAILED:
        return ()
    reasons: list[str] = []
    if snapshot.cycles.consecutive_failures > 0:
        reasons.append(CONSECUTIVE_FAILURES_PRESENT)
    if snapshot.retry_after.active:
        reasons.append(RETRY_AFTER_ACTIVE)
    return tuple(reasons)


def degraded_conditions(snapshot: OperatorSnapshot) -> tuple[str, ...]:
    """``DEGRADED``: not ``FAILED``, not ``RECOVERING``, last cycle succeeded.

    At least one supporting-subsystem condition must hold:

    * ``outbox.pending_count > 0``
    * ``outbox.failed_count > 0``
    * ``outbox.healthy is False``
    * ``checkpoint.last_persist_success is False``
    * ``delivery.last_failure_category is not None``

    The rule is gated on ``cycles.last_cycle_success is True``; a loop whose last
    cycle did not succeed is not "degraded", it is unresolved (``UNKNOWN``).
    """

    _require_snapshot(snapshot)
    if snapshot.lifecycle.state is LifecycleState.FAILED:
        return ()
    if recovering_conditions(snapshot):
        return ()
    if snapshot.cycles.last_cycle_success is not True:
        return ()
    reasons: list[str] = []
    if snapshot.outbox.pending_count > 0:
        reasons.append(OUTBOX_PENDING)
    if snapshot.outbox.failed_count > 0:
        reasons.append(OUTBOX_FAILED)
    if snapshot.outbox.healthy is False:
        reasons.append(OUTBOX_UNHEALTHY)
    if snapshot.checkpoint.last_persist_success is False:
        reasons.append(CHECKPOINT_PERSIST_FAILED)
    if snapshot.delivery.last_failure_category is not None:
        reasons.append(DELIVERY_FAILURE_PRESENT)
    return tuple(reasons)


def unknown_conditions(snapshot: OperatorSnapshot) -> tuple[str, ...]:
    """``UNKNOWN``: required health information cannot be established.

    Reached only when the snapshot is not ``FAILED``, ``RECOVERING``, or
    ``DEGRADED``. Applies when:

    * ``cycles.completed_cycles == 0`` — nothing has been observed yet;
    * ``cycles.last_cycle_success is None`` — the latest outcome is unknown;
    * the lifecycle is not ``RUNNING`` — health cannot be affirmed for a loop
      that is not polling, which is exactly the clean ``STOPPED`` case;
    * ``cycles.last_cycle_success is False`` without a failure counter or
      Retry-After — the loop is not affirmatively healthy.
    """

    _require_snapshot(snapshot)
    if snapshot.lifecycle.state is LifecycleState.FAILED:
        return ()
    if recovering_conditions(snapshot):
        return ()
    if degraded_conditions(snapshot):
        return ()
    reasons: list[str] = []
    if snapshot.cycles.completed_cycles == 0:
        reasons.append(NO_COMPLETED_CYCLES)
    if snapshot.cycles.last_cycle_success is None:
        reasons.append(LAST_CYCLE_OUTCOME_UNESTABLISHED)
    if snapshot.lifecycle.state is not LifecycleState.RUNNING:
        reasons.append(LIFECYCLE_NOT_RUNNING)
    if snapshot.cycles.last_cycle_success is False:
        reasons.append(LAST_CYCLE_UNSUCCESSFUL)
    return tuple(reasons)


def unmet_health_requirements(snapshot: OperatorSnapshot) -> tuple[str, ...]:
    """Every ``HEALTHY`` requirement the snapshot does not satisfy.

    ``HEALTHY`` requires all of:

    * ``lifecycle.state is RUNNING``
    * ``cycles.completed_cycles > 0``
    * ``cycles.last_cycle_success is True``
    * ``cycles.consecutive_failures == 0``
    * ``retry_after.active is False``
    * no ``DEGRADED`` condition
    * no ``UNKNOWN`` condition

    An empty tuple means the snapshot is ``HEALTHY`` and all required health
    information is available.
    """

    _require_snapshot(snapshot)
    reasons: list[str] = []
    if snapshot.lifecycle.state is not LifecycleState.RUNNING:
        reasons.append(LIFECYCLE_NOT_RUNNING)
    if snapshot.cycles.completed_cycles == 0:
        reasons.append(NO_COMPLETED_CYCLES)
    if snapshot.cycles.last_cycle_success is None:
        reasons.append(LAST_CYCLE_OUTCOME_UNESTABLISHED)
    elif snapshot.cycles.last_cycle_success is False:
        reasons.append(LAST_CYCLE_UNSUCCESSFUL)
    if snapshot.cycles.consecutive_failures > 0:
        reasons.append(CONSECUTIVE_FAILURES_PRESENT)
    if snapshot.retry_after.active:
        reasons.append(RETRY_AFTER_ACTIVE)
    reasons.extend(degraded_conditions(snapshot))
    reasons.extend(unknown_conditions(snapshot))
    # Deterministic, order-stable, and free of duplicates.
    return tuple(dict.fromkeys(reasons))


def classify_health(snapshot: OperatorSnapshot) -> OperatorHealthState:
    """Classify one snapshot using the locked first-match precedence.

    Total and deterministic: every valid snapshot resolves to exactly one of the
    five states, and the fail-closed terminal branch means an unforeseen
    combination resolves to ``UNKNOWN`` and never to ``HEALTHY``.
    """

    _require_snapshot(snapshot)
    if failed_conditions(snapshot):
        return OperatorHealthState.FAILED
    if recovering_conditions(snapshot):
        return OperatorHealthState.RECOVERING
    if degraded_conditions(snapshot):
        return OperatorHealthState.DEGRADED
    if unknown_conditions(snapshot):
        return OperatorHealthState.UNKNOWN
    if not unmet_health_requirements(snapshot):
        return OperatorHealthState.HEALTHY
    # Unreachable for a valid snapshot: every requirement is covered above.
    # Fail closed rather than affirm health.
    return OperatorHealthState.UNKNOWN


__all__ = [
    "CHECKPOINT_PERSIST_FAILED",
    "CONSECUTIVE_FAILURES_PRESENT",
    "DELIVERY_FAILURE_PRESENT",
    "HEALTH_PRECEDENCE",
    "LAST_CYCLE_OUTCOME_UNESTABLISHED",
    "LAST_CYCLE_UNSUCCESSFUL",
    "LIFECYCLE_NOT_RUNNING",
    "NO_COMPLETED_CYCLES",
    "OUTBOX_FAILED",
    "OUTBOX_PENDING",
    "OUTBOX_UNHEALTHY",
    "RETRY_AFTER_ACTIVE",
    "OperatorHealthState",
    "classify_health",
    "degraded_conditions",
    "failed_conditions",
    "recovering_conditions",
    "unmet_health_requirements",
    "unknown_conditions",
]
