"""Phase 25B-3 session tests: lifecycle, containment, retention, run identity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from smcsignal.monitoring import (
    CounterMetric,
    FixedClock,
    HealthCode,
    HealthEvent,
    HealthState,
    MonitoredComponent,
    MonitoredRun,
    MonitoringConfig,
    MonitoringInputError,
    MonitoringSession,
    NullMonitor,
    RecordingMonitor,
    build_health_event,
    run_identity,
)

START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LATER = START + timedelta(minutes=5)


class SteppingClock:
    """A small mutable clock so a test can move the run forward between calls."""

    def __init__(self, instant: datetime, monotonic_seconds: float = 0.0) -> None:
        self.instant = instant
        self.monotonic_seconds = monotonic_seconds

    def wall_clock(self) -> datetime:
        return self.instant

    def monotonic(self) -> float:
        return self.monotonic_seconds


class ExplodingMonitor:
    """A monitor that fails every write, used to prove failure containment."""

    def __init__(self, *, fail_metrics: bool = True) -> None:
        self.fail_metrics = fail_metrics
        self.calls = 0

    def record(self, event: HealthEvent) -> None:
        self.calls += 1
        raise RuntimeError("monitor is broken")

    def record_metric(self, metric: object) -> None:
        self.calls += 1
        if self.fail_metrics:
            raise RuntimeError("monitor is broken")

    def events(self) -> tuple[HealthEvent, ...]:
        return ()

    def metrics(self) -> tuple[object, ...]:
        return ()


def _event(
    code: HealthCode = HealthCode.DELIVERY_TIMEOUT,
    *,
    component: MonitoredComponent = MonitoredComponent.DELIVERY,
    observed_at: datetime = START,
    subject_id: str | None = None,
) -> HealthEvent:
    return build_health_event(
        component=component,
        code=code,
        observed_at=observed_at,
        subject_id=subject_id,
    )


def _session(
    *,
    config: MonitoringConfig | None = None,
    monitor: object | None = None,
    clock: object | None = None,
    label: str | None = None,
) -> MonitoringSession:
    return MonitoringSession.begin(
        clock=clock or SteppingClock(START),  # type: ignore[arg-type]
        config=config or MonitoringConfig(),
        monitor=monitor or RecordingMonitor(),  # type: ignore[arg-type]
        label=label,
    )


# --- run lifecycle ----------------------------------------------------------


def test_begin_creates_a_labelled_deterministic_run() -> None:
    run = MonitoredRun.begin(clock=FixedClock(instant=START), label="prod-eu")
    assert run.run_id.startswith("run:")
    assert run.started_at == START
    assert run.label == "prod-eu"
    assert run == MonitoredRun.begin(clock=FixedClock(instant=START), label="prod-eu")


def test_run_identity_depends_on_the_instant_and_the_label() -> None:
    first = MonitoredRun.begin(clock=FixedClock(instant=START), label="a")
    second = MonitoredRun.begin(clock=FixedClock(instant=LATER), label="a")
    third = MonitoredRun.begin(clock=FixedClock(instant=START), label="b")
    assert len({first.run_id, second.run_id, third.run_id}) == 3


def test_run_identity_is_a_pure_function_of_its_content() -> None:
    run = MonitoredRun.begin(clock=FixedClock(instant=START), label="a")
    assert run.run_id == run_identity(started_at=START, label="a")


@pytest.mark.parametrize("label", ["", "  x", "x ", "a" * 65, "with space", "tok:en", 5])
def test_run_rejects_an_unsafe_label(label: object) -> None:
    with pytest.raises(MonitoringInputError):
        MonitoredRun.begin(clock=FixedClock(instant=START), label=label)  # type: ignore[arg-type]


def test_run_rejects_naive_timestamps_and_unprefixed_ids() -> None:
    with pytest.raises(MonitoringInputError):
        MonitoredRun(run_id="run:abc", started_at=datetime(2026, 1, 1))
    with pytest.raises(MonitoringInputError):
        MonitoredRun(run_id="abc", started_at=START)


def test_begin_records_run_started_and_starts_unknown() -> None:
    session = _session()
    assert session.ended is False
    assert session.observed_events == 1
    # Everything is UNKNOWN except the lifecycle component, which an INFO
    # condition leaves untouched.
    assert set(session.health_map().values()) == {HealthState.UNKNOWN}


def test_end_completes_the_run_and_settles_its_lifecycle() -> None:
    clock = SteppingClock(START)
    session = _session(clock=clock)
    clock.instant = LATER
    report = session.end()
    assert session.ended is True
    assert report.failed is False
    assert report.run is session.run
    assert report.ended_at == LATER
    assert report.state(MonitoredComponent.RUN_LIFECYCLE) is HealthState.HEALTHY


def test_failed_end_marks_the_lifecycle_failing() -> None:
    clock = SteppingClock(START)
    session = _session(clock=clock)
    clock.instant = LATER
    report = session.end(failed=True)
    assert report.failed is True
    assert report.state(MonitoredComponent.RUN_LIFECYCLE) is HealthState.FAILING


def test_begin_closes_run_started_and_end_closes_the_terminal_condition() -> None:
    clock = SteppingClock(START)
    session = _session(clock=clock)
    clock.instant = LATER
    report = session.end()
    codes = {aggregate.code for aggregate in report.aggregates}
    assert HealthCode.RUN_STARTED in codes
    assert HealthCode.RUN_COMPLETED in codes


def test_a_run_ends_exactly_once() -> None:
    session = _session()
    session.end()
    with pytest.raises(MonitoringInputError):
        session.end()


@pytest.mark.parametrize("action", ["record", "record_metric"])
def test_a_closed_run_refuses_further_observations(action: str) -> None:
    session = _session()
    session.end()
    with pytest.raises(MonitoringInputError):
        if action == "record":
            session.record(_event())
        else:
            session.record_metric(CounterMetric(name="c"))


def test_end_rejects_a_non_boolean_failed_flag() -> None:
    with pytest.raises(MonitoringInputError):
        _session().end(failed="yes")  # type: ignore[arg-type]


def test_end_rejects_a_clock_that_moved_backwards() -> None:
    clock = SteppingClock(START)
    session = _session(clock=clock)
    clock.instant = START - timedelta(seconds=1)
    with pytest.raises(MonitoringInputError):
        session.end()


# --- recording --------------------------------------------------------------


def test_recording_a_condition_degrades_only_that_component() -> None:
    session = _session()
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    health = session.health_map()
    assert health[MonitoredComponent.DELIVERY] is HealthState.DEGRADED
    assert health[MonitoredComponent.MARKET_DATA] is HealthState.UNKNOWN


def test_recording_a_critical_condition_fails_the_component() -> None:
    session = _session()
    session.record(
        _event(HealthCode.DATA_CONFLICTING_DUPLICATE, component=MonitoredComponent.DATA_INTEGRITY)
    )
    assert session.health(MonitoredComponent.DATA_INTEGRITY) is HealthState.FAILING


def test_recording_deduplicates_identical_conditions() -> None:
    session = _session()
    for _ in range(3):
        session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    report = session.end()
    matching = [
        aggregate
        for aggregate in report.aggregates
        if aggregate.code is HealthCode.DELIVERY_TIMEOUT
    ]
    assert len(matching) == 1
    assert matching[0].count == 3


def test_recording_never_modifies_the_observed_event() -> None:
    session = _session()
    event = _event(HealthCode.DELIVERY_FAILED)
    before = event.to_record()
    session.record(event)
    assert event.to_record() == before
    assert session.end().aggregates  # the event was still recorded


def test_record_rejects_a_foreign_value() -> None:
    session = _session()
    with pytest.raises(MonitoringInputError):
        session.record("not-an-event")  # type: ignore[arg-type]


def test_record_metric_rejects_a_foreign_value() -> None:
    session = _session()
    with pytest.raises(MonitoringInputError):
        session.record_metric("not-a-metric")  # type: ignore[arg-type]


def test_health_map_is_a_copy() -> None:
    session = _session()
    view = session.health_map()
    view[MonitoredComponent.DELIVERY] = HealthState.FAILING
    assert session.health(MonitoredComponent.DELIVERY) is HealthState.UNKNOWN


def test_health_rejects_a_foreign_component() -> None:
    with pytest.raises(MonitoringInputError):
        _session().health("delivery")  # type: ignore[arg-type]


def test_metrics_reach_the_monitor_and_the_report() -> None:
    monitor = RecordingMonitor()
    session = _session(monitor=monitor)
    counter = CounterMetric(name="delivery.attempted").increment(4)
    session.record_metric(counter)
    report = session.end()
    assert monitor.metrics() == (counter,)
    assert report.metrics.get("delivery.attempted") == counter


# --- retention cap ----------------------------------------------------------


def test_retention_cap_drops_events_but_still_counts_them() -> None:
    # The cap is two, and begin already retains RUN_STARTED, so only the first
    # condition of the five fits.
    config = MonitoringConfig(max_events_per_run=2)
    session = _session(config=config)
    for _ in range(5):
        session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    assert session.dropped_observations == 4
    report = session.end()
    # The terminal condition is dropped too, and health still reflects every
    # condition seen, retained or not.
    assert report.dropped_observations == 5
    assert report.state(MonitoredComponent.DELIVERY) is HealthState.DEGRADED


def test_the_drop_is_explained_by_a_quarantined_condition() -> None:
    config = MonitoringConfig(max_events_per_run=1)
    session = _session(config=config)
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    report = session.end()
    codes = {aggregate.code for aggregate in report.aggregates}
    assert HealthCode.MONITOR_OBSERVATION_DROPPED in codes


def test_the_quarantine_is_bounded_by_distinct_codes() -> None:
    config = MonitoringConfig(max_events_per_run=1)
    session = _session(config=config)
    for _ in range(50):
        session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    report = session.end()
    dropped = [
        aggregate
        for aggregate in report.aggregates
        if aggregate.code is HealthCode.MONITOR_OBSERVATION_DROPPED
    ]
    # Fifty conditions plus the terminal one, collapsed into a single aggregate.
    assert len(dropped) == 1
    assert dropped[0].count == 51
    assert report.dropped_observations == 51


# --- failure containment ----------------------------------------------------


def test_a_failing_monitor_is_contained_and_never_propagates() -> None:
    monitor = ExplodingMonitor()
    session = _session(monitor=monitor)
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))  # must not raise
    assert session.self_failures >= 1
    report = session.end()
    assert report.self_failures >= 1
    assert report.state(MonitoredComponent.MONITORING) is HealthState.FAILING


def test_a_failing_monitor_is_recorded_as_a_monitoring_defect() -> None:
    session = _session(monitor=ExplodingMonitor())
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    report = session.end()
    codes = {aggregate.code for aggregate in report.aggregates}
    assert HealthCode.MONITOR_INTERNAL_FAILURE in codes


def test_a_failing_monitor_does_not_lose_the_observed_condition() -> None:
    session = _session(monitor=ExplodingMonitor())
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    report = session.end()
    # The condition was observed, so health reflects it even though the monitor
    # could not carry it.
    assert report.state(MonitoredComponent.DELIVERY) is HealthState.DEGRADED


def test_a_monitor_that_fails_repeatedly_stays_bounded() -> None:
    session = _session(monitor=ExplodingMonitor())
    for _ in range(30):
        session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    report = session.end()
    internal = [
        aggregate
        for aggregate in report.aggregates
        if aggregate.code is HealthCode.MONITOR_INTERNAL_FAILURE
    ]
    assert len(internal) == 1
    # Thirty conditions, plus RUN_STARTED from begin and RUN_COMPLETED from end.
    assert session.self_failures == 32


def test_a_monitor_failing_on_metrics_is_also_contained() -> None:
    session = _session(monitor=ExplodingMonitor())
    session.record_metric(CounterMetric(name="c"))  # must not raise
    assert session.self_failures == 2  # RUN_STARTED from begin, plus the metric


def test_the_null_monitor_never_causes_a_self_failure() -> None:
    session = _session(monitor=NullMonitor())
    session.record(_event(HealthCode.DELIVERY_TIMEOUT))
    session.record_metric(CounterMetric(name="c"))
    report = session.end()
    assert report.self_failures == 0
    assert report.dropped_observations == 0
    assert report.aggregates  # the session still holds the truth itself


# --- construction guards ----------------------------------------------------


def test_the_session_rejects_bad_construction_arguments() -> None:
    run = MonitoredRun.begin(clock=FixedClock(instant=START))
    clock = FixedClock(instant=START)
    config = MonitoringConfig()
    monitor = NullMonitor()
    with pytest.raises(MonitoringInputError):
        MonitoringSession(run="run:x", clock=clock, config=config, monitor=monitor)  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        MonitoringSession(run=run, clock="clock", config=config, monitor=monitor)  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        MonitoringSession(run=run, clock=clock, config="config", monitor=monitor)  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        MonitoringSession(run=run, clock=clock, config=config, monitor=object())  # type: ignore[arg-type]
