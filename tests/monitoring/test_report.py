"""Phase 25B-3 report tests: aggregation, validation, determinism, identity."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from smcsignal.monitoring import (
    CounterMetric,
    DurationMetric,
    FixedClock,
    HealthCode,
    HealthEvent,
    HealthEventAggregate,
    HealthState,
    MetricSummary,
    MonitoredComponent,
    MonitoredRun,
    MonitoringConfig,
    MonitoringInputError,
    MonitoringReport,
    MonitoringSession,
    NullMonitor,
    advance_aggregate,
    aggregate_events,
    build_health_event,
    canonical_record,
    merge_aggregates,
    require_serializable,
    unobserved,
)

START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LATER = START + timedelta(minutes=1)
LATEST = START + timedelta(minutes=2)


def _event(
    code: HealthCode = HealthCode.DELIVERY_TIMEOUT,
    *,
    component: MonitoredComponent = MonitoredComponent.DELIVERY,
    observed_at: datetime = START,
) -> HealthEvent:
    return build_health_event(component=component, code=code, observed_at=observed_at)


def _run(*, label: str | None = None, instant: datetime = START) -> MonitoredRun:
    return MonitoredRun.begin(clock=FixedClock(instant=instant), label=label)


def _health(
    overrides: dict[MonitoredComponent, HealthState] | None = None,
) -> tuple[tuple[MonitoredComponent, HealthState], ...]:
    states = unobserved()
    if overrides:
        states.update(overrides)
    return tuple(sorted(states.items(), key=lambda pair: pair[0].value))


def _report(
    *,
    health: tuple[tuple[MonitoredComponent, HealthState], ...] | None = None,
    aggregates: tuple[HealthEventAggregate, ...] = (),
    metrics: MetricSummary | None = None,
    ended_at: datetime = LATER,
    failed: bool = False,
    observed_events: int = 0,
    dropped_observations: int = 0,
    self_failures: int = 0,
) -> MonitoringReport:
    return MonitoringReport(
        run=_run(),
        ended_at=ended_at,
        failed=failed,
        health=health if health is not None else _health(),
        aggregates=aggregates,
        metrics=metrics if metrics is not None else MetricSummary(),
        observed_events=observed_events,
        dropped_observations=dropped_observations,
        self_failures=self_failures,
    )


# --- aggregation ------------------------------------------------------------


def test_aggregate_events_deduplicates_by_content_identity() -> None:
    event = _event()
    aggregates = aggregate_events([event, event, event])
    assert len(aggregates) == 1
    assert aggregates[0].count == 3
    assert aggregates[0].code is HealthCode.DELIVERY_TIMEOUT


def test_aggregate_events_widens_the_seen_range() -> None:
    aggregates = aggregate_events([_event(observed_at=START), _event(observed_at=LATEST)])
    assert aggregates[0].first_seen == START
    assert aggregates[0].last_seen == LATEST


def test_aggregate_events_tolerates_an_out_of_order_sequence() -> None:
    # Report assembly must stay total even if a clock stepped backwards.
    aggregates = aggregate_events([_event(observed_at=LATEST), _event(observed_at=START)])
    assert aggregates[0].first_seen == START
    assert aggregates[0].last_seen == LATEST
    assert aggregates[0].count == 2


def test_aggregate_events_sorts_by_identity_and_separates_conditions() -> None:
    events = [
        _event(HealthCode.DELIVERY_FAILED),
        _event(HealthCode.DELIVERY_TIMEOUT),
        _event(HealthCode.DELIVERY_FAILED),
    ]
    aggregates = aggregate_events(events)
    identities = [aggregate.event_id for aggregate in aggregates]
    assert identities == sorted(identities)
    assert {aggregate.code for aggregate in aggregates} == {
        HealthCode.DELIVERY_FAILED,
        HealthCode.DELIVERY_TIMEOUT,
    }


def test_aggregate_events_rejects_foreign_values() -> None:
    with pytest.raises(MonitoringInputError):
        aggregate_events(["not-an-event"])  # type: ignore[list-item]


def test_advance_aggregate_rejects_a_foreign_value() -> None:
    with pytest.raises(MonitoringInputError):
        advance_aggregate("nope", START)  # type: ignore[arg-type]


def test_merge_aggregates_sums_counts_and_widens_ranges() -> None:
    first = aggregate_events([_event(observed_at=LATER)])
    second = aggregate_events([_event(observed_at=START), _event(observed_at=LATEST)])
    merged = merge_aggregates(first, second)
    assert len(merged) == 1
    assert merged[0].count == 3
    assert merged[0].first_seen == START
    assert merged[0].last_seen == LATEST


def test_merge_aggregates_rejects_conflicting_conditions_for_one_identity() -> None:
    aggregate = aggregate_events([_event()])[0]
    conflicting = HealthEventAggregate(
        event_id=aggregate.event_id,
        component=aggregate.component,
        severity=aggregate.severity,
        code=aggregate.code,
        count=2,
        first_seen=START,
        last_seen=LATEST,
    )
    forged = HealthEventAggregate(
        event_id=conflicting.event_id,
        component=MonitoredComponent.MARKET_DATA,
        severity=conflicting.severity,
        code=conflicting.code,
        count=1,
        first_seen=START,
        last_seen=START,
    )
    with pytest.raises(MonitoringInputError):
        merge_aggregates((conflicting,), (forged,))


def test_merge_aggregates_rejects_foreign_values() -> None:
    with pytest.raises(MonitoringInputError):
        merge_aggregates(("nope",))  # type: ignore[arg-type]


# --- report validation ------------------------------------------------------


def test_report_derives_overall_from_its_health_map() -> None:
    report = _report(health=_health({MonitoredComponent.DELIVERY: HealthState.FAILING}))
    assert report.overall is HealthState.FAILING
    assert _report().overall is HealthState.UNKNOWN


def test_report_reads_one_component_and_rejects_a_foreign_one() -> None:
    report = _report()
    assert report.state(MonitoredComponent.DELIVERY) is HealthState.UNKNOWN
    with pytest.raises(MonitoringInputError):
        report.state("delivery")  # type: ignore[arg-type]
    assert set(report.health_map()) == set(MonitoredComponent)


@pytest.mark.parametrize(
    "health",
    [
        (),  # nothing stated
        ((MonitoredComponent.DELIVERY, HealthState.HEALTHY),),  # almost everything missing
    ],
)
def test_report_requires_a_complete_health_map(health: object) -> None:
    with pytest.raises(MonitoringInputError):
        _report(health=health)  # type: ignore[arg-type]


def test_report_rejects_duplicate_health_entries() -> None:
    doubled = _health() + _health()
    with pytest.raises(MonitoringInputError):
        _report(health=doubled)


def test_report_rejects_a_malformed_health_entry() -> None:
    with pytest.raises(MonitoringInputError):
        _report(health=(MonitoredComponent.DELIVERY, HealthState.HEALTHY))  # type: ignore[arg-type]


def test_report_requires_a_sorted_health_map() -> None:
    reversed_health = tuple(reversed(_health()))
    with pytest.raises(MonitoringInputError):
        _report(health=reversed_health)


def test_report_requires_sorted_unique_aggregates() -> None:
    first = aggregate_events([_event(HealthCode.DELIVERY_FAILED)])[0]
    second = aggregate_events([_event(HealthCode.DELIVERY_TIMEOUT)])[0]
    ordered = tuple(sorted((first, second), key=lambda item: item.event_id))
    assert _report(aggregates=ordered).aggregates == ordered
    with pytest.raises(MonitoringInputError):
        _report(aggregates=tuple(reversed(ordered)))
    with pytest.raises(MonitoringInputError):
        _report(aggregates=(ordered[0], ordered[0]))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ended_at": START - timedelta(seconds=1)},  # before the run start
        {"ended_at": datetime(2026, 1, 1, 12, 30)},  # naive
        {"failed": "no"},
        {"observed_events": -1},
        {"dropped_observations": 1},  # more drops than observations
        {"self_failures": True},
        {"metrics": "summary"},
    ],
)
def test_report_rejects_inconsistent_content(kwargs: dict[str, object]) -> None:
    with pytest.raises(MonitoringInputError):
        _report(**kwargs)  # type: ignore[arg-type]


def test_report_rejects_a_non_run_and_foreign_health_entries() -> None:
    with pytest.raises(MonitoringInputError):
        MonitoringReport(
            run="run:x",  # type: ignore[arg-type]
            ended_at=LATER,
            failed=False,
            health=_health(),
            aggregates=(),
            metrics=MetricSummary(),
            observed_events=0,
            dropped_observations=0,
            self_failures=0,
        )
    with pytest.raises(MonitoringInputError):
        _report(health=(("delivery", HealthState.HEALTHY),))  # type: ignore[arg-type]


# --- identity and serialization --------------------------------------------


def test_report_identity_has_the_documented_prefix_and_is_deterministic() -> None:
    report = _report()
    assert report.report_id.startswith("monitoring-report:")
    assert report.report_id == _report().report_id
    assert report.to_record()["report_id"] == report.report_id


def test_report_identity_changes_with_the_content() -> None:
    baseline = _report().report_id
    assert _report(ended_at=LATEST).report_id != baseline
    assert _report(failed=True).report_id != baseline
    assert (
        _report(health=_health({MonitoredComponent.DELIVERY: HealthState.FAILING})).report_id
        != baseline
    )
    assert _report(aggregates=aggregate_events([_event()])).report_id != baseline
    assert _report(observed_events=1).report_id != baseline
    assert _report(metrics=MetricSummary.of(CounterMetric(name="c"))).report_id != baseline


def test_report_record_is_serializable_and_float_free() -> None:
    record = _report(
        aggregates=aggregate_events([_event()]),
        metrics=MetricSummary.of(
            DurationMetric(name="latency").record(timedelta(milliseconds=250))
        ),
        observed_events=1,
    ).to_record()
    require_serializable(record)
    rendered = canonical_record(record)
    assert '"methodology":"monitoring-report-v1"' in rendered
    # Durations reach the record as exact Decimal milliseconds, never as floats
    # or raw timedeltas: 250ms is canonically 25e1.
    assert '"total_milliseconds":"25e1"' in rendered


def test_report_is_immutable() -> None:
    report = _report()
    with pytest.raises(FrozenInstanceError):
        report.failed = True  # type: ignore[misc]


# --- session-produced determinism ------------------------------------------


def _produce() -> MonitoringReport:
    session = MonitoringSession.begin(
        clock=FixedClock(instant=START),
        config=MonitoringConfig(),
        monitor=NullMonitor(),
        label="prod",
    )
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    session.record_metric(CounterMetric(name="delivery.attempted").increment(2))
    return session.end()


def test_a_session_report_is_byte_identical_across_runs() -> None:
    first, second = _produce(), _produce()
    assert first.report_id == second.report_id
    assert first == second
    assert canonical_record(first.to_record()) == canonical_record(second.to_record())


def test_a_session_report_is_complete_without_a_downstream_monitor() -> None:
    report = _produce()
    # Only the delivery component degraded and the lifecycle settled, so the
    # rollup is DEGRADED while every untouched component is still UNKNOWN.
    assert report.overall is HealthState.DEGRADED
    assert report.state(MonitoredComponent.MARKET_DATA) is HealthState.UNKNOWN
    assert report.state(MonitoredComponent.RUN_LIFECYCLE) is HealthState.HEALTHY
    assert report.state(MonitoredComponent.DELIVERY) is HealthState.DEGRADED
    assert report.observed_events == 4
    assert report.metrics.get("delivery.attempted") is not None
    assert {aggregate.code for aggregate in report.aggregates} >= {
        HealthCode.RUN_STARTED,
        HealthCode.RUN_COMPLETED,
        HealthCode.DELIVERY_TIMEOUT,
    }


def test_run_record_is_part_of_the_report_and_serializable() -> None:
    report = _produce()
    record = report.to_record()
    assert record["run"]["run_id"] == report.run.run_id  # type: ignore[index]
    assert record["run"]["label"] == "prod"  # type: ignore[index]
