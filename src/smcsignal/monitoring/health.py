"""Pure, deterministic health transitions and precedence rollup.

Health is a function of what a run has observed, not a score and not a stored
policy. Two rules define the model:

* An event can only ever make a component's health worse or leave it unchanged.
  Severity sets a floor - ``WARNING`` floors at ``DEGRADED`` and ``CRITICAL``
  floors at ``FAILING`` - while an informational event changes nothing. A
  condition can therefore never silently clear an open anomaly, and a caller can
  never inflate or suppress the effect of a code.
* A component that has not been observed is ``UNKNOWN``, and ``UNKNOWN`` outranks
  ``HEALTHY`` when health is rolled up, so an unobserved component can never
  contribute to a false all-clear.

Every function here is pure: same inputs, same output, no ambient time, no
ambient state, and no mutation of the caller's values. ``rollup`` deliberately
requires every component to be stated, including the unobserved ones, because a
partial map would be a silent all-clear; ``unobserved`` builds the complete map
for a run that has observed nothing.

Recovery policy is not decided here. Whether an observer clears an anomaly after
a clean observation, and how recency interacts with that, is a later-phase
decision; ``clear`` is the explicit primitive such a policy would call.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from smcsignal.monitoring.errors import MonitoringInputError
from smcsignal.monitoring.models import (
    HealthEvent,
    HealthState,
    MonitoredComponent,
    Severity,
)

# The precedence order, most severe first. It is the single source of truth for
# both the rollup and the transition floors, so the two can never disagree.
HEALTH_PRECEDENCE: tuple[HealthState, ...] = (
    HealthState.FAILING,
    HealthState.DEGRADED,
    HealthState.UNKNOWN,
    HealthState.HEALTHY,
)

# The state of a component nothing has been observed about yet. A run starts
# here, never at HEALTHY.
INITIAL_HEALTH_STATE = HealthState.UNKNOWN

_RANK: Mapping[HealthState, int] = MappingProxyType(
    {state: len(HEALTH_PRECEDENCE) - 1 - index for index, state in enumerate(HEALTH_PRECEDENCE)}
)

# Severity floors. ``None`` means "informational: this condition changes no
# health state", which is why an INFO event cannot signal a recovery either.
_FLOOR_BY_SEVERITY: Mapping[Severity, HealthState | None] = MappingProxyType(
    {
        Severity.INFO: None,
        Severity.WARNING: HealthState.DEGRADED,
        Severity.CRITICAL: HealthState.FAILING,
    }
)


def rank(state: HealthState) -> int:
    """Return the severity rank of a state; a larger rank is worse."""
    if not isinstance(state, HealthState):
        raise MonitoringInputError("state must be a HealthState")
    return _RANK[state]


def worse_of(left: HealthState, right: HealthState) -> HealthState:
    """Return the more severe of two states; ties keep the left operand."""
    if not isinstance(left, HealthState) or not isinstance(right, HealthState):
        raise MonitoringInputError("worse_of requires two HealthState values")
    return left if _RANK[left] >= _RANK[right] else right


def unobserved() -> dict[MonitoredComponent, HealthState]:
    """Return a complete component map with every component ``UNKNOWN``.

    This is the health map of a run that has observed nothing. It is the
    required starting point for ``rollup``, whose completeness rule exists so an
    unobserved component can never be silently dropped from a verdict.
    """
    return {component: INITIAL_HEALTH_STATE for component in MonitoredComponent}


def rollup(states: Mapping[MonitoredComponent, HealthState]) -> HealthState:
    """Return the worst-known state across a complete component map.

    The result is ``FAILING`` if any component is failing, otherwise
    ``DEGRADED`` if any is degraded, otherwise ``UNKNOWN`` if any is unknown, and
    only ``HEALTHY`` when every component is healthy. Every component must be
    present: omitting one would report a more favourable verdict than the
    observations support, so an incomplete map is rejected rather than filled in.
    """
    if not isinstance(states, Mapping):
        raise MonitoringInputError("rollup requires a mapping of component to health state")
    missing = sorted(set(MonitoredComponent) - set(states))
    if missing:
        raise MonitoringInputError(
            "rollup requires every component to be stated, including unobserved ones: "
            + ", ".join(component.value for component in missing)
        )
    resolved: list[HealthState] = []
    for component, state in states.items():
        if not isinstance(component, MonitoredComponent):
            raise MonitoringInputError("rollup keys must be MonitoredComponent values")
        if not isinstance(state, HealthState):
            raise MonitoringInputError("rollup values must be HealthState values")
        resolved.append(state)
    if not resolved:  # pragma: no cover - the completeness check above rejects this
        return INITIAL_HEALTH_STATE
    # Fold from the first observed state, never from a seeded verdict: seeding
    # with UNKNOWN would make an entirely healthy map report UNKNOWN.
    verdict = resolved[0]
    for state in resolved[1:]:
        verdict = worse_of(verdict, state)
    return verdict


def transition(current: HealthState, event: HealthEvent) -> HealthState:
    """Apply one observed condition to a component's health.

    Pure and monotone: the severity of the condition sets a floor and the result
    is the worse of that floor and the current state. An informational condition
    leaves health untouched, and no condition ever improves health - only
    ``clear`` can, and only when a caller asks for it explicitly.
    """
    if not isinstance(current, HealthState):
        raise MonitoringInputError("current must be a HealthState")
    if not isinstance(event, HealthEvent):
        raise MonitoringInputError("event must be a HealthEvent")
    floor = _FLOOR_BY_SEVERITY[event.severity]
    if floor is None:
        return current
    return worse_of(current, floor)


def clear(current: HealthState) -> HealthState:
    """Return ``HEALTHY``: close every open anomaly for a component.

    Clearing is a deliberate act. An anomaly is never closed by the passage of
    time, by a lower-severity condition arriving, or by an informational event,
    which is why this is a separate primitive rather than a transition.
    """
    if not isinstance(current, HealthState):
        raise MonitoringInputError("current must be a HealthState")
    return HealthState.HEALTHY
