"""Phase 35F: the five-state deterministic health classification.

The centrepiece is :func:`test_every_valid_snapshot_maps_to_exactly_one_state`,
which enumerates the entire valid snapshot space, classifies each member, and
checks the result against an *independent* oracle transcribed directly from the
validated rules — so the implementation is proved against the contract rather
than against itself.

Offline and pure: no clock, no IO, no transport, and no loop is mutated.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.models import FailureCategory
from smcsignal.live import (
    OPERATOR_SNAPSHOT_SCHEMA_VERSION,
    CheckpointView,
    ConfigurationView,
    CycleView,
    DeliveryView,
    LifecycleState,
    LifecycleView,
    OperatorHealthState,
    OperatorSnapshot,
    OperatorSnapshotValidationError,
    OutboxView,
    RetryAfterView,
    classify_health,
)
from smcsignal.live.health import (
    CHECKPOINT_PERSIST_FAILED,
    CONSECUTIVE_FAILURES_PRESENT,
    DELIVERY_FAILURE_PRESENT,
    HEALTH_PRECEDENCE,
    LAST_CYCLE_OUTCOME_UNESTABLISHED,
    LAST_CYCLE_UNSUCCESSFUL,
    LIFECYCLE_NOT_RUNNING,
    NO_COMPLETED_CYCLES,
    OUTBOX_FAILED,
    OUTBOX_PENDING,
    OUTBOX_UNHEALTHY,
    RETRY_AFTER_ACTIVE,
    degraded_conditions,
    failed_conditions,
    recovering_conditions,
    unknown_conditions,
    unmet_health_requirements,
)

GENERATED_AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

LIFECYCLES = (LifecycleState.RUNNING, LifecycleState.STOPPED, LifecycleState.FAILED)
CYCLE_COUNTS = (0, 3)
FAILURE_COUNTS = (0, 2)
LAST_SUCCESS = (None, True, False)
RETRY_ACTIVE = (False, True)
CHECKPOINT = (True, False, None)
PENDING = (0, 1)
OUTBOX_FAILED_COUNT = (0, 1)
OUTBOX_HEALTHY = (True, False)
DELIVERY_CATEGORY: tuple[FailureCategory | None, ...] = (None, FailureCategory.TRANSPORT)


def snapshot(
    *,
    state: LifecycleState = LifecycleState.RUNNING,
    completed: int = 3,
    failures: int = 0,
    last_success: bool | None = True,
    retry_active: bool = False,
    checkpoint: bool | None = True,
    pending: int = 0,
    outbox_failed: int = 0,
    outbox_healthy: bool = True,
    category: FailureCategory | None = None,
) -> OperatorSnapshot:
    """A fully-specified snapshot; each parameter maps to one validated field."""

    return OperatorSnapshot(
        schema_version=OPERATOR_SNAPSHOT_SCHEMA_VERSION,
        generated_at=GENERATED_AT,
        lifecycle=LifecycleView(state=state),
        configuration=ConfigurationView(identity="configuration:test"),
        cycles=CycleView(completed, failures, last_success),
        checkpoint=CheckpointView(last_persist_success=checkpoint),
        retry_after=RetryAfterView(
            active=retry_active, delay_seconds=30.0 if retry_active else None
        ),
        outbox=OutboxView(pending, outbox_failed, outbox_healthy),
        delivery=DeliveryView(last_failure_category=category),
    )


def valid_snapshots() -> list[OperatorSnapshot]:
    """The whole cross product, filtered to those the validator accepts."""

    found: list[OperatorSnapshot] = []
    for combo in itertools.product(
        LIFECYCLES,
        CYCLE_COUNTS,
        FAILURE_COUNTS,
        LAST_SUCCESS,
        RETRY_ACTIVE,
        CHECKPOINT,
        PENDING,
        OUTBOX_FAILED_COUNT,
        OUTBOX_HEALTHY,
        DELIVERY_CATEGORY,
    ):
        (
            state,
            completed,
            failures,
            last_success,
            retry_active,
            checkpoint,
            pending,
            outbox_failed,
            outbox_healthy,
            category,
        ) = combo
        try:
            found.append(
                snapshot(
                    state=state,
                    completed=completed,
                    failures=failures,
                    last_success=last_success,
                    retry_active=retry_active,
                    checkpoint=checkpoint,
                    pending=pending,
                    outbox_failed=outbox_failed,
                    outbox_healthy=outbox_healthy,
                    category=category,
                )
            )
        except OperatorSnapshotValidationError:
            continue
    return found


def expected_state(candidate: OperatorSnapshot) -> OperatorHealthState:
    """Independent oracle, transcribed from the validated Phase 35F rules."""

    cycles = candidate.cycles
    outbox = candidate.outbox

    # FAILED: lifecycle.state == FAILED
    if candidate.lifecycle.state is LifecycleState.FAILED:
        return OperatorHealthState.FAILED

    # RECOVERING: not FAILED and (consecutive_failures > 0 or retry_after.active)
    if cycles.consecutive_failures > 0 or candidate.retry_after.active:
        return OperatorHealthState.RECOVERING

    degraded = (
        outbox.pending_count > 0
        or outbox.failed_count > 0
        or outbox.healthy is False
        or candidate.checkpoint.last_persist_success is False
        or candidate.delivery.last_failure_category is not None
    )
    # DEGRADED: not FAILED, not RECOVERING, last_cycle_success == true, and a
    # degraded condition.
    if cycles.last_cycle_success is True and degraded:
        return OperatorHealthState.DEGRADED

    # UNKNOWN: not FAILED/RECOVERING/DEGRADED and required health information
    # cannot be established.
    unknown = (
        cycles.completed_cycles == 0
        or cycles.last_cycle_success is None
        or cycles.last_cycle_success is False
        or candidate.lifecycle.state is not LifecycleState.RUNNING
    )
    if unknown:
        return OperatorHealthState.UNKNOWN

    # HEALTHY: running, cycles observed and successful, no failures, no
    # Retry-After, no degraded and no unknown condition.
    healthy = (
        candidate.lifecycle.state is LifecycleState.RUNNING
        and cycles.completed_cycles > 0
        and cycles.last_cycle_success is True
        and cycles.consecutive_failures == 0
        and candidate.retry_after.active is False
        and not degraded
    )
    if healthy:
        return OperatorHealthState.HEALTHY
    return OperatorHealthState.UNKNOWN


# -- the vocabulary -------------------------------------------------------------


def test_exactly_five_health_states_exist() -> None:
    assert tuple(state.value for state in OperatorHealthState) == (
        "FAILED",
        "RECOVERING",
        "DEGRADED",
        "UNKNOWN",
        "HEALTHY",
    )
    assert len(OperatorHealthState) == 5


def test_no_gap_or_recovery_state_exists() -> None:
    assert not hasattr(OperatorHealthState, "GAP")
    assert not hasattr(OperatorHealthState, "RECOVERY")
    assert "GAP" not in {state.value for state in OperatorHealthState}
    assert "RECOVERY" not in {state.value for state in OperatorHealthState}


def test_no_invalid_health_state_exists() -> None:
    """``UNKNOWN`` is a state; ``INVALID`` is not — invalid input is rejected."""

    assert not hasattr(OperatorHealthState, "INVALID")
    assert "INVALID" not in {state.value for state in OperatorHealthState}


def test_the_precedence_constant_is_the_locked_first_match_order() -> None:
    assert HEALTH_PRECEDENCE == ("FAILED", "RECOVERING", "DEGRADED", "UNKNOWN", "HEALTHY")


# -- FAILED ---------------------------------------------------------------------


def test_failed_lifecycle_maps_to_failed() -> None:
    assert classify_health(snapshot(state=LifecycleState.FAILED)) is OperatorHealthState.FAILED


def test_failed_outranks_every_other_condition() -> None:
    """First match: a FAILED lifecycle wins even with a full degraded backlog."""

    worst = snapshot(
        state=LifecycleState.FAILED,
        completed=5,
        failures=3,
        last_success=False,
        retry_active=True,
        checkpoint=False,
        pending=4,
        outbox_failed=2,
        outbox_healthy=False,
        category=FailureCategory.NETWORK,
    )
    assert classify_health(worst) is OperatorHealthState.FAILED
    assert failed_conditions(worst) == ("lifecycle_state_failed",)
    # The lower rules report nothing for a FAILED lifecycle.
    assert recovering_conditions(worst) == ()
    assert degraded_conditions(worst) == ()
    assert unknown_conditions(worst) == ()


def test_a_clean_failed_lifecycle_is_still_failed() -> None:
    clean = snapshot(
        state=LifecycleState.FAILED,
        completed=1,
        failures=0,
        last_success=False,
        retry_active=False,
        checkpoint=True,
    )
    assert classify_health(clean) is OperatorHealthState.FAILED


# -- RECOVERING -----------------------------------------------------------------


def test_consecutive_failures_alone_map_to_recovering() -> None:
    candidate = snapshot(completed=3, failures=2, last_success=False)
    assert classify_health(candidate) is OperatorHealthState.RECOVERING
    assert recovering_conditions(candidate) == (CONSECUTIVE_FAILURES_PRESENT,)


def test_an_active_retry_after_alone_maps_to_recovering() -> None:
    candidate = snapshot(completed=3, failures=0, last_success=True, retry_active=True)
    assert classify_health(candidate) is OperatorHealthState.RECOVERING
    assert recovering_conditions(candidate) == (RETRY_AFTER_ACTIVE,)


def test_both_recovering_conditions_are_reported_together() -> None:
    candidate = snapshot(completed=3, failures=2, last_success=False, retry_active=True)
    assert classify_health(candidate) is OperatorHealthState.RECOVERING
    assert recovering_conditions(candidate) == (
        CONSECUTIVE_FAILURES_PRESENT,
        RETRY_AFTER_ACTIVE,
    )


def test_recovering_outranks_degraded() -> None:
    candidate = snapshot(
        completed=3,
        failures=1,
        last_success=False,
        pending=5,
        outbox_healthy=False,
        checkpoint=False,
        category=FailureCategory.TIMEOUT,
    )
    assert classify_health(candidate) is OperatorHealthState.RECOVERING
    # DEGRADED is gated on a non-FAILED, non-RECOVERING, successful last cycle.
    assert degraded_conditions(candidate) == ()


def test_recovering_applies_to_a_stopped_lifecycle() -> None:
    candidate = snapshot(state=LifecycleState.STOPPED, completed=3, failures=1, last_success=False)
    assert classify_health(candidate) is OperatorHealthState.RECOVERING


# -- DEGRADED -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"pending": 1}, OUTBOX_PENDING),
        ({"outbox_failed": 1}, OUTBOX_FAILED),
        ({"outbox_healthy": False}, OUTBOX_UNHEALTHY),
        ({"checkpoint": False}, CHECKPOINT_PERSIST_FAILED),
        ({"category": FailureCategory.TRANSPORT}, DELIVERY_FAILURE_PRESENT),
    ],
)
def test_each_degraded_condition_on_its_own_maps_to_degraded(
    kwargs: dict[str, object], code: str
) -> None:
    candidate = snapshot(**kwargs)  # type: ignore[arg-type]
    assert classify_health(candidate) is OperatorHealthState.DEGRADED
    assert code in degraded_conditions(candidate)


def test_all_degraded_conditions_are_reported_together() -> None:
    candidate = snapshot(
        pending=2,
        outbox_failed=1,
        outbox_healthy=False,
        checkpoint=False,
        category=FailureCategory.RATE_LIMIT,
    )
    assert classify_health(candidate) is OperatorHealthState.DEGRADED
    assert degraded_conditions(candidate) == (
        OUTBOX_PENDING,
        OUTBOX_FAILED,
        OUTBOX_UNHEALTHY,
        CHECKPOINT_PERSIST_FAILED,
        DELIVERY_FAILURE_PRESENT,
    )


def test_degraded_requires_a_successful_last_cycle() -> None:
    """A non-successful last cycle is unresolved, not merely degraded."""

    unresolved = snapshot(completed=3, failures=0, last_success=False, pending=2)
    assert classify_health(unresolved) is not OperatorHealthState.DEGRADED
    assert degraded_conditions(unresolved) == ()


def test_an_unpersisted_checkpoint_on_its_own_degrades_a_healthy_loop() -> None:
    candidate = snapshot(checkpoint=False)
    assert classify_health(candidate) is OperatorHealthState.DEGRADED
    assert degraded_conditions(candidate) == (CHECKPOINT_PERSIST_FAILED,)


def test_a_null_checkpoint_state_is_not_a_degraded_condition() -> None:
    """Tri-state matters: ``None`` (not attempted) is not ``False``."""

    assert snapshot(checkpoint=None).checkpoint.last_persist_success is None
    assert CHECKPOINT_PERSIST_FAILED not in degraded_conditions(snapshot(checkpoint=None))


def test_a_present_failure_category_degrades_even_with_a_clean_outbox() -> None:
    candidate = snapshot(
        pending=0, outbox_failed=0, outbox_healthy=True, category=FailureCategory.BUILD
    )
    assert classify_health(candidate) is OperatorHealthState.DEGRADED


# -- UNKNOWN --------------------------------------------------------------------


def test_pre_first_cycle_maps_to_unknown_never_healthy() -> None:
    candidate = snapshot(
        state=LifecycleState.RUNNING,
        completed=0,
        failures=0,
        last_success=None,
        retry_active=False,
        checkpoint=None,
    )
    assert classify_health(candidate) is OperatorHealthState.UNKNOWN
    reasons = unknown_conditions(candidate)
    assert NO_COMPLETED_CYCLES in reasons
    assert LAST_CYCLE_OUTCOME_UNESTABLISHED in reasons
    # Zero failures must never be read as observed success.
    assert unmet_health_requirements(candidate) != ()


def test_a_clean_stopped_lifecycle_maps_to_unknown() -> None:
    candidate = snapshot(
        state=LifecycleState.STOPPED,
        completed=9,
        failures=0,
        last_success=True,
        retry_active=False,
        checkpoint=True,
        pending=0,
        outbox_failed=0,
        outbox_healthy=True,
        category=None,
    )
    assert classify_health(candidate) is OperatorHealthState.UNKNOWN
    assert unknown_conditions(candidate) == (LIFECYCLE_NOT_RUNNING,)
    assert LIFECYCLE_NOT_RUNNING in unmet_health_requirements(candidate)


def test_a_stopped_lifecycle_with_a_degraded_condition_is_degraded() -> None:
    candidate = snapshot(state=LifecycleState.STOPPED, pending=1)
    assert classify_health(candidate) is OperatorHealthState.DEGRADED


def test_an_unsuccessful_last_cycle_without_a_counter_maps_to_unknown() -> None:
    """A fatal, non-recoverable failure increments no counter, so it is unresolved."""

    candidate = snapshot(completed=1, failures=0, last_success=False)
    assert classify_health(candidate) is OperatorHealthState.UNKNOWN
    assert unknown_conditions(candidate) == (LAST_CYCLE_UNSUCCESSFUL,)


def test_an_unestablished_last_cycle_outcome_maps_to_unknown() -> None:
    candidate = snapshot(completed=0, failures=0, last_success=None)
    assert classify_health(candidate) is OperatorHealthState.UNKNOWN


def test_unknown_requires_the_higher_rules_not_to_fire() -> None:
    candidate = snapshot(completed=0, failures=0, last_success=None)
    assert unknown_conditions(candidate) != ()
    failed = snapshot(state=LifecycleState.FAILED, completed=0, failures=0, last_success=None)
    assert unknown_conditions(failed) == ()


# -- HEALTHY --------------------------------------------------------------------


def test_the_fully_healthy_snapshot_maps_to_healthy() -> None:
    candidate = snapshot(
        state=LifecycleState.RUNNING,
        completed=12,
        failures=0,
        last_success=True,
        retry_active=False,
        checkpoint=True,
        pending=0,
        outbox_failed=0,
        outbox_healthy=True,
        category=None,
    )
    assert classify_health(candidate) is OperatorHealthState.HEALTHY
    assert unmet_health_requirements(candidate) == ()
    assert unknown_conditions(candidate) == ()
    assert degraded_conditions(candidate) == ()


def test_a_null_checkpoint_state_does_not_block_healthy_on_its_own() -> None:
    """``None`` is not a DEGRADED condition; only ``False`` is."""

    candidate = snapshot(checkpoint=None)
    assert candidate.checkpoint.last_persist_success is None
    assert classify_health(candidate) is OperatorHealthState.HEALTHY


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state": LifecycleState.STOPPED},
        {"completed": 0, "failures": 0, "last_success": None, "checkpoint": None},
        {"last_success": False},
        {"retry_active": True},
        {"pending": 1},
        {"outbox_failed": 1},
        {"outbox_healthy": False},
        {"checkpoint": False},
        {"category": FailureCategory.NETWORK},
    ],
)
def test_every_single_deviation_blocks_healthy(kwargs: dict[str, object]) -> None:
    candidate = snapshot(**kwargs)  # type: ignore[arg-type]
    assert classify_health(candidate) is not OperatorHealthState.HEALTHY
    assert unmet_health_requirements(candidate) != ()


def test_a_positive_failure_counter_blocks_healthy() -> None:
    candidate = snapshot(completed=4, failures=2, last_success=False)
    assert classify_health(candidate) is not OperatorHealthState.HEALTHY
    assert CONSECUTIVE_FAILURES_PRESENT in unmet_health_requirements(candidate)


def test_unmet_requirements_are_deduplicated_and_stable() -> None:
    candidate = snapshot(
        state=LifecycleState.STOPPED,
        completed=0,
        failures=0,
        last_success=None,
        pending=1,
        checkpoint=None,
    )
    reasons = unmet_health_requirements(candidate)
    assert len(reasons) == len(set(reasons))
    assert reasons == unmet_health_requirements(candidate)


# -- determinism, purity, and totality ------------------------------------------


def test_classification_is_deterministic_and_side_effect_free() -> None:
    candidate = snapshot(completed=4, failures=1, last_success=False, retry_active=True)
    before = asdict(candidate)
    first = classify_health(candidate)
    for _ in range(5):
        assert classify_health(candidate) is first
    assert asdict(candidate) == before


def test_equal_snapshots_classify_identically() -> None:
    assert classify_health(snapshot()) == classify_health(replace(snapshot()))


def test_every_valid_snapshot_maps_to_exactly_one_state() -> None:
    """Totality + agreement with the independent oracle over the whole space."""

    candidates = valid_snapshots()
    # The enumeration must be meaningful, not vacuously small.
    assert len(candidates) > 100

    buckets: dict[OperatorHealthState, int] = {state: 0 for state in OperatorHealthState}
    for candidate in candidates:
        observed = classify_health(candidate)
        # exactly one state, and a real member of the closed vocabulary
        assert isinstance(observed, OperatorHealthState)
        assert observed in OperatorHealthState
        # repeatable
        assert classify_health(candidate) is observed
        # agrees with the independently transcribed rules
        assert observed is expected_state(candidate), candidate
        buckets[observed] += 1

    # The five buckets partition the space: nothing is unclassified.
    assert sum(buckets.values()) == len(candidates)
    # Every one of the five states is actually reachable.
    for state, count in buckets.items():
        assert count > 0, f"{state} is unreachable"


def test_the_classification_is_a_partition_of_the_valid_space() -> None:
    candidates = valid_snapshots()
    classified: dict[OperatorHealthState, list[OperatorSnapshot]] = {
        state: [] for state in OperatorHealthState
    }
    for candidate in candidates:
        classified[classify_health(candidate)].append(candidate)
    total = sum(len(group) for group in classified.values())
    assert total == len(candidates)
    for first, second in itertools.combinations(classified.values(), 2):
        assert not any(candidate in second for candidate in first)


def test_every_health_state_has_at_least_one_witness() -> None:
    witnesses = {
        OperatorHealthState.FAILED: snapshot(state=LifecycleState.FAILED),
        OperatorHealthState.RECOVERING: snapshot(failures=1, last_success=False),
        OperatorHealthState.DEGRADED: snapshot(pending=1),
        OperatorHealthState.UNKNOWN: snapshot(state=LifecycleState.STOPPED),
        OperatorHealthState.HEALTHY: snapshot(),
    }
    for state, candidate in witnesses.items():
        assert classify_health(candidate) is state


def test_the_first_matching_rule_always_decides() -> None:
    """Precedence is first-match, so a higher rule always shadows a lower one."""

    cases = [
        (
            snapshot(state=LifecycleState.FAILED, failures=2, last_success=False, pending=3),
            OperatorHealthState.FAILED,
        ),
        (
            snapshot(failures=2, last_success=False, pending=3, outbox_healthy=False),
            OperatorHealthState.RECOVERING,
        ),
        (
            snapshot(pending=3, outbox_healthy=False, checkpoint=False),
            OperatorHealthState.DEGRADED,
        ),
        (
            snapshot(state=LifecycleState.STOPPED, pending=0),
            OperatorHealthState.UNKNOWN,
        ),
        (
            snapshot(),
            OperatorHealthState.HEALTHY,
        ),
    ]
    for candidate, expected in cases:
        assert classify_health(candidate) is expected


# -- input contract -------------------------------------------------------------


def test_classification_rejects_a_non_snapshot() -> None:
    with pytest.raises(AnalysisInputError):
        classify_health(None)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        classify_health({"lifecycle": {"state": "FAILED"}})  # type: ignore[arg-type]
    for probe in (
        failed_conditions,
        recovering_conditions,
        degraded_conditions,
        unknown_conditions,
        unmet_health_requirements,
    ):
        with pytest.raises(AnalysisInputError):
            probe("not-a-snapshot")  # type: ignore[arg-type]


def test_an_invalid_snapshot_cannot_reach_classification() -> None:
    """UNKNOWN is a state; INVALID is a rejection that happens first."""

    with pytest.raises(OperatorSnapshotValidationError):
        snapshot(completed=1, failures=5, last_success=True)
    # There is no INVALID state to fall back to: classification refuses a
    # non-snapshot outright instead of inventing one.
    with pytest.raises(AnalysisInputError):
        classify_health(object())  # type: ignore[arg-type]


def test_the_health_module_holds_no_state_of_its_own() -> None:
    import smcsignal.live.health as health

    for name, value in vars(health).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (list, dict, set)), f"health.py holds mutable state: {name}"
    # Classification is a pure function of the snapshot alone.
    assert callable(health.classify_health)


def test_the_health_module_never_imports_monitoring_or_persistence() -> None:
    import ast
    from pathlib import Path

    import smcsignal.live.health as health

    tree = ast.parse(Path(health.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported <= {
        "__future__",
        "enum",
        "smcsignal.analysis.errors",
        "smcsignal.live.operator",
    }
    assert not any(name.startswith("smcsignal.monitoring") for name in imported)
    assert not any(name.startswith("smcsignal.persistence") for name in imported)
