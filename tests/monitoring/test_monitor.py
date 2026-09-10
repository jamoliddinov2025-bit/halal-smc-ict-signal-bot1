"""Phase 25B-3 monitor tests: protocol conformance and the two shipped monitors."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smcsignal.monitoring import (
    CounterMetric,
    GaugeMetric,
    HealthCode,
    HealthEvent,
    Monitor,
    MonitoredComponent,
    MonitoringInputError,
    NullMonitor,
    RatioMetric,
    RecordingMonitor,
    build_health_event,
    require_metric,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _event(code: HealthCode = HealthCode.DELIVERY_TIMEOUT) -> HealthEvent:
    return build_health_event(component=MonitoredComponent.DELIVERY, code=code, observed_at=NOW)


def test_both_monitors_satisfy_the_protocol() -> None:
    assert isinstance(NullMonitor(), Monitor)
    assert isinstance(RecordingMonitor(), Monitor)


def test_a_custom_object_can_satisfy_the_protocol() -> None:
    class _Collector:
        def __init__(self) -> None:
            self.seen: list[HealthEvent] = []

        def record(self, event: HealthEvent) -> None:
            self.seen.append(event)

        def record_metric(self, metric: object) -> None:
            return None

    assert isinstance(_Collector(), Monitor)


def test_null_monitor_observes_nothing_and_never_raises() -> None:
    monitor = NullMonitor()
    for _ in range(3):
        assert monitor.record(_event()) is None
        assert monitor.record_metric(CounterMetric(name="c")) is None


def test_null_monitor_is_immutable_and_has_no_attributes() -> None:
    monitor = NullMonitor()
    monitor.record(_event())  # a call mutates nothing
    # Slotted and stateless: there is no instance dictionary to hold anything.
    assert not hasattr(monitor, "__dict__")
    with pytest.raises(AttributeError):
        assert monitor.retained  # type: ignore[attr-defined]
    assert monitor == NullMonitor()


def test_recording_monitor_captures_events_in_order() -> None:
    monitor = RecordingMonitor()
    first = _event(HealthCode.DELIVERY_TIMEOUT)
    second = _event(HealthCode.DELIVERY_FAILED)
    monitor.record(first)
    monitor.record(second)
    assert monitor.events() == (first, second)
    assert monitor.metrics() == ()


def test_recording_monitor_captures_metrics_in_order() -> None:
    monitor = RecordingMonitor()
    counter = CounterMetric(name="attempted").increment(2)
    ratio = RatioMetric(name="success", numerator=1, denominator=2)
    monitor.record_metric(counter)
    monitor.record_metric(ratio)
    assert monitor.metrics() == (counter, ratio)


def test_recording_monitor_reads_are_snapshots() -> None:
    monitor = RecordingMonitor()
    monitor.record(_event())
    snapshot = monitor.events()
    monitor.record(_event(HealthCode.DELIVERY_FAILED))
    assert len(snapshot) == 1
    assert isinstance(snapshot, tuple)
    assert len(monitor.events()) == 2


def test_recording_monitor_never_rewrites_what_it_stored() -> None:
    monitor = RecordingMonitor()
    event = _event()
    monitor.record(event)
    monitor.record(_event(HealthCode.DELIVERY_FAILED))
    assert monitor.events()[0] is event
    assert monitor.events()[0].to_record() == event.to_record()


def test_recording_monitor_rejects_foreign_values() -> None:
    monitor = RecordingMonitor()
    with pytest.raises(MonitoringInputError):
        monitor.record("not-an-event")  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        monitor.record_metric("not-a-metric")  # type: ignore[arg-type]
    assert monitor.events() == ()
    assert monitor.metrics() == ()


def test_require_metric_accepts_the_metric_layer() -> None:
    assert require_metric(CounterMetric(name="c")) == CounterMetric(name="c")
    assert require_metric(RatioMetric(name="r", numerator=1, denominator=2)).denominator == 2
    assert require_metric(GaugeMetric(name="g", value=Decimal(1), observed_at=NOW))


@pytest.mark.parametrize("value", ["metric", 1, None, (1, 2)])
def test_require_metric_rejects_anything_else(value: object) -> None:
    with pytest.raises(MonitoringInputError):
        require_metric(value)
