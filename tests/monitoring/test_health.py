"""Phase 25B-2 health tests: pure transitions, precedence rollup, no side effects."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smcsignal.monitoring import (
    HEALTH_PRECEDENCE,
    INITIAL_HEALTH_STATE,
    HealthCode,
    HealthEvent,
    HealthState,
    MonitoredComponent,
    MonitoringInputError,
    build_health_event,
    clear,
    rank,
    rollup,
    transition,
    unobserved,
    worse_of,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

# One representative event per severity, taken from the fixed severity table.
INFO_EVENT = (MonitoredComponent.RUN_LIFECYCLE, HealthCode.RUN_STARTED)
WARNING_EVENT = (MonitoredComponent.DELIVERY, HealthCode.DELIVERY_TIMEOUT)
CRITICAL_EVENT = (MonitoredComponent.DATA_INTEGRITY, HealthCode.DATA_CONFLICTING_DUPLICATE)


def _event(pair: tuple[MonitoredComponent, HealthCode]) -> HealthEvent:
    component, code = pair
    return build_health_event(component=component, code=code, observed_at=NOW)


def test_precedence_covers_exactly_the_four_states() -> None:
    assert set(HEALTH_PRECEDENCE) == set(HealthState)
    assert len(HEALTH_PRECEDENCE) == 4
    assert HEALTH_PRECEDENCE[0] is HealthState.FAILING


def test_initial_state_is_unknown_never_healthy() -> None:
    assert INITIAL_HEALTH_STATE is HealthState.UNKNOWN


def test_rank_orders_severity() -> None:
    assert rank(HealthState.FAILING) > rank(HealthState.DEGRADED)
    assert rank(HealthState.DEGRADED) > rank(HealthState.UNKNOWN)
    assert rank(HealthState.UNKNOWN) > rank(HealthState.HEALTHY)


def test_rank_rejects_a_non_state() -> None:
    with pytest.raises(MonitoringInputError):
        rank("failing")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (HealthState.HEALTHY, HealthState.UNKNOWN, HealthState.UNKNOWN),
        (HealthState.UNKNOWN, HealthState.HEALTHY, HealthState.UNKNOWN),
        (HealthState.UNKNOWN, HealthState.DEGRADED, HealthState.DEGRADED),
        (HealthState.DEGRADED, HealthState.FAILING, HealthState.FAILING),
        (HealthState.DEGRADED, HealthState.HEALTHY, HealthState.DEGRADED),
        (HealthState.FAILING, HealthState.HEALTHY, HealthState.FAILING),
        (HealthState.DEGRADED, HealthState.DEGRADED, HealthState.DEGRADED),
        (HealthState.FAILING, HealthState.FAILING, HealthState.FAILING),
    ],
)
def test_worse_of_precedence(left: HealthState, right: HealthState, expected: HealthState) -> None:
    assert worse_of(left, right) is expected


def test_worse_of_rejects_non_states() -> None:
    with pytest.raises(MonitoringInputError):
        worse_of(HealthState.HEALTHY, "healthy")  # type: ignore[arg-type]


def test_worse_of_is_commutative_in_severity() -> None:
    for left in HealthState:
        for right in HealthState:
            assert rank(worse_of(left, right)) == rank(worse_of(right, left))


def test_unobserved_map_is_complete_and_all_unknown() -> None:
    states = unobserved()
    assert set(states) == set(MonitoredComponent)
    assert set(states.values()) == {HealthState.UNKNOWN}
    # Each call returns a fresh mapping so a run cannot share mutable health.
    states[MonitoredComponent.DELIVERY] = HealthState.FAILING
    assert unobserved()[MonitoredComponent.DELIVERY] is HealthState.UNKNOWN


@pytest.mark.parametrize(
    ("severity_event", "expected"),
    [
        (INFO_EVENT, HealthState.HEALTHY),
        (WARNING_EVENT, HealthState.DEGRADED),
        (CRITICAL_EVENT, HealthState.FAILING),
    ],
)
def test_transition_applies_the_severity_floor(
    severity_event: tuple[MonitoredComponent, HealthCode], expected: HealthState
) -> None:
    assert transition(HealthState.HEALTHY, _event(severity_event)) is expected


@pytest.mark.parametrize("current", list(HealthState))
def test_informational_conditions_never_change_health(current: HealthState) -> None:
    # An INFO code cannot clear an anomaly and cannot create one.
    assert transition(current, _event(INFO_EVENT)) is current


@pytest.mark.parametrize("current", list(HealthState))
def test_transition_never_improves_health(current: HealthState) -> None:
    for pair in (INFO_EVENT, WARNING_EVENT, CRITICAL_EVENT):
        assert rank(transition(current, _event(pair))) >= rank(current)


def test_transition_is_idempotent_and_deterministic() -> None:
    event = _event(WARNING_EVENT)
    once = transition(HealthState.HEALTHY, event)
    twice = transition(once, event)
    assert once is HealthState.DEGRADED
    assert twice is HealthState.DEGRADED
    assert transition(HealthState.HEALTHY, event) is once


def test_transition_does_not_mutate_the_event() -> None:
    event = _event(CRITICAL_EVENT)
    before = event.to_record()
    transition(HealthState.HEALTHY, event)
    transition(HealthState.FAILING, event)
    assert event.to_record() == before


@pytest.mark.parametrize(
    ("current", "event"),
    [("healthy", None), (HealthState.HEALTHY, "event")],
)
def test_transition_rejects_bad_arguments(current: object, event: object) -> None:
    with pytest.raises(MonitoringInputError):
        transition(current, event)  # type: ignore[arg-type]


@pytest.mark.parametrize("current", list(HealthState))
def test_clear_returns_healthy_from_every_state(current: HealthState) -> None:
    assert clear(current) is HealthState.HEALTHY


def test_clear_rejects_a_non_state() -> None:
    with pytest.raises(MonitoringInputError):
        clear("degraded")  # type: ignore[arg-type]


def test_rollup_of_all_healthy_is_healthy() -> None:
    assert rollup(dict.fromkeys(MonitoredComponent, HealthState.HEALTHY)) is HealthState.HEALTHY


def test_rollup_never_reports_healthy_while_anything_is_unknown() -> None:
    states = dict.fromkeys(MonitoredComponent, HealthState.HEALTHY)
    states[MonitoredComponent.MONITORING] = HealthState.UNKNOWN
    assert rollup(states) is HealthState.UNKNOWN


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({MonitoredComponent.DELIVERY: HealthState.DEGRADED}, HealthState.DEGRADED),
        ({MonitoredComponent.DELIVERY: HealthState.FAILING}, HealthState.FAILING),
        ({MonitoredComponent.DELIVERY: HealthState.UNKNOWN}, HealthState.UNKNOWN),
        (
            {
                MonitoredComponent.DELIVERY: HealthState.UNKNOWN,
                MonitoredComponent.MARKET_DATA: HealthState.DEGRADED,
            },
            HealthState.DEGRADED,
        ),
        (
            {
                MonitoredComponent.DELIVERY: HealthState.DEGRADED,
                MonitoredComponent.MARKET_DATA: HealthState.FAILING,
            },
            HealthState.FAILING,
        ),
    ],
)
def test_rollup_is_worst_known(
    overrides: dict[MonitoredComponent, HealthState], expected: HealthState
) -> None:
    states = dict.fromkeys(MonitoredComponent, HealthState.HEALTHY)
    states.update(overrides)
    assert rollup(states) is expected


def test_rollup_of_a_new_run_is_unknown() -> None:
    assert rollup(unobserved()) is HealthState.UNKNOWN


def test_rollup_rejects_an_incomplete_map() -> None:
    # A missing component would otherwise be dropped from the verdict and could
    # turn a partial picture into a false all-clear.
    complete = dict.fromkeys(MonitoredComponent, HealthState.HEALTHY)
    del complete[MonitoredComponent.TELEGRAM_TRANSPORT]
    with pytest.raises(MonitoringInputError):
        rollup(complete)


def test_rollup_rejects_unknown_keys_and_values() -> None:
    complete = dict.fromkeys(MonitoredComponent, HealthState.HEALTHY)
    # A genuinely foreign key. Note that a plain string equal to a member's
    # value is indistinguishable from that member, because these enums are
    # StrEnum - that is inherent to the enum style used across the project.
    with pytest.raises(MonitoringInputError):
        rollup({**complete, "not_a_component": HealthState.HEALTHY})  # type: ignore[dict-item]
    with pytest.raises(MonitoringInputError):
        rollup({**complete, MonitoredComponent.DELIVERY: "failing"})  # type: ignore[dict-item]
    with pytest.raises(MonitoringInputError):
        rollup([HealthState.HEALTHY])  # type: ignore[arg-type]


def test_rollup_does_not_modify_the_caller_map() -> None:
    states = unobserved()
    states[MonitoredComponent.DELIVERY] = HealthState.DEGRADED
    snapshot = dict(states)
    rollup(states)
    assert states == snapshot


def test_health_module_is_pure_and_repeatable() -> None:
    states = unobserved()
    states[MonitoredComponent.SIGNAL_ENGINE] = HealthState.DEGRADED
    first = [transition(state, _event(WARNING_EVENT)) for state in HealthState]
    second = [transition(state, _event(WARNING_EVENT)) for state in HealthState]
    assert first == second
    assert rollup(states) == rollup(states)
