"""Phase 25B-2 metric tests: exact types, Decimal handling, immutability."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext

import pytest

from smcsignal.analysis.liquidity.evidence import evidence_json
from smcsignal.monitoring import (
    CounterMetric,
    DurationMetric,
    GaugeMetric,
    MetricSummary,
    MonitoringInputError,
    RatioMetric,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _floats_in(value: object) -> list[object]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        found: list[object] = []
        for key, item in value.items():
            found.extend(_floats_in(key))
            found.extend(_floats_in(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_floats_in(item))
        return found
    return []


# --- counters ---------------------------------------------------------------


def test_counter_starts_at_zero_and_is_exact() -> None:
    counter = CounterMetric(name="delivery.attempted")
    assert counter.total == 0
    assert isinstance(counter.total, int)


def test_increment_returns_a_new_counter_and_never_mutates() -> None:
    counter = CounterMetric(name="delivery.failed")
    once = counter.increment()
    twice = once.increment()
    assert (counter.total, once.total, twice.total) == (0, 1, 2)
    assert counter is not once


def test_increment_by_an_explicit_amount() -> None:
    counter = CounterMetric(name="data.rows").increment(5)
    assert counter.total == 5


@pytest.mark.parametrize("amount", [0, -1, 1.5, True, "2"])
def test_increment_rejects_a_non_positive_amount(amount: object) -> None:
    with pytest.raises(MonitoringInputError):
        CounterMetric(name="c").increment(amount)  # type: ignore[arg-type]


@pytest.mark.parametrize("total", [-1, 1.5, True, "1"])
def test_counter_rejects_a_non_integer_total(total: object) -> None:
    with pytest.raises(MonitoringInputError):
        CounterMetric(name="c", total=total)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "  ", " padded", 1, None])
def test_metrics_reject_a_bad_name(name: object) -> None:
    with pytest.raises(MonitoringInputError):
        CounterMetric(name=name)  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        RatioMetric(name=name, numerator=1, denominator=1)  # type: ignore[arg-type]


def test_counter_is_immutable() -> None:
    counter = CounterMetric(name="c")
    with pytest.raises(FrozenInstanceError):
        counter.total = 9  # type: ignore[misc]


def test_counter_record_is_float_free() -> None:
    record = CounterMetric(name="c").increment(3).to_record()
    assert record == {"kind": "counter", "name": "c", "total": 3}
    assert _floats_in(record) == []


# --- durations --------------------------------------------------------------


def test_empty_duration_measures_nothing() -> None:
    metric = DurationMetric(name="delivery.latency")
    assert metric.count == 0
    assert metric.total == timedelta(0)
    assert metric.average is None
    assert metric.to_record()["total_milliseconds"] == Decimal("0E-3")


def test_record_tracks_count_total_minimum_and_maximum() -> None:
    metric = DurationMetric(name="delivery.latency")
    for measured in (timedelta(seconds=3), timedelta(seconds=1), timedelta(seconds=5)):
        metric = metric.record(measured)
    assert metric.count == 3
    assert metric.total == timedelta(seconds=9)
    assert metric.minimum == timedelta(seconds=1)
    assert metric.maximum == timedelta(seconds=5)


def test_record_never_mutates_the_original() -> None:
    empty = DurationMetric(name="d")
    filled = empty.record(timedelta(seconds=2))
    assert empty.count == 0
    assert filled.count == 1
    assert empty.total == timedelta(0)


def test_average_is_exact_decimal_microseconds() -> None:
    metric = (
        DurationMetric(name="d").record(timedelta(microseconds=1)).record(timedelta(microseconds=2))
    )
    assert metric.average == Decimal("1.5")


def test_average_is_independent_of_the_ambient_decimal_context() -> None:
    metric = (
        DurationMetric(name="d").record(timedelta(microseconds=1)).record(timedelta(microseconds=2))
    )
    ratio = RatioMetric(name="r", numerator=2, denominator=3)
    expected_average = metric.average
    expected_ratio = ratio.value
    baseline = getcontext().prec
    try:
        getcontext().prec = 3
        # The results must not change when a process-wide context changes.
        assert metric.average == expected_average
        assert ratio.value == expected_ratio
    finally:
        getcontext().prec = baseline
    assert expected_average == Decimal("1.5")
    assert expected_ratio == Decimal("0.6666666666666666666666666667")


@pytest.mark.parametrize("duration", [timedelta(microseconds=-1), timedelta(seconds=-5)])
def test_record_rejects_a_negative_measurement(duration: timedelta) -> None:
    with pytest.raises(MonitoringInputError):
        DurationMetric(name="d").record(duration)


def test_record_rejects_a_non_timedelta() -> None:
    with pytest.raises(MonitoringInputError):
        DurationMetric(name="d").record(1.5)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": 0, "total": timedelta(seconds=1)},
        {"count": 0, "minimum": timedelta(seconds=1)},
        {
            "count": 3,
            "total": timedelta(seconds=1),
            "minimum": timedelta(seconds=2),
            "maximum": timedelta(seconds=2),
        },
        {
            "count": 1,
            "total": timedelta(seconds=1),
            "minimum": timedelta(seconds=1),
            "maximum": timedelta(seconds=9),
        },
        {"count": -1},
        {"count": True},
        {"count": 1, "total": timedelta(seconds=-1)},
    ],
)
def test_duration_rejects_inconsistent_aggregates(kwargs: dict[str, object]) -> None:
    with pytest.raises(MonitoringInputError):
        DurationMetric(name="d", **kwargs)  # type: ignore[arg-type]


def test_duration_record_uses_exact_millisecond_scaling() -> None:
    metric = DurationMetric(name="d").record(timedelta(microseconds=1500))
    record = metric.to_record()
    assert record["total_milliseconds"] == Decimal("1.5")
    assert record["minimum_milliseconds"] == Decimal("1.5")
    assert _floats_in(record) == []


# --- ratios -----------------------------------------------------------------


def test_ratio_is_an_exact_decimal() -> None:
    ratio = RatioMetric(name="delivery.success", numerator=3, denominator=4)
    assert ratio.value == Decimal("0.75")


def test_ratio_with_a_zero_denominator_reports_nothing() -> None:
    # None rather than zero or an infinity: no observation exists to report.
    assert RatioMetric(name="r", numerator=0, denominator=0).value is None
    assert RatioMetric(name="r", numerator=5, denominator=0).value is None


@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_ratio_rejects_bad_counts(value: object) -> None:
    with pytest.raises(MonitoringInputError):
        RatioMetric(name="r", numerator=value, denominator=1)  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        RatioMetric(name="r", numerator=1, denominator=value)  # type: ignore[arg-type]


def test_ratio_record_serializes_the_optional_value() -> None:
    assert RatioMetric(name="r", numerator=1, denominator=2).to_record()["value"] == Decimal("0.5")
    assert RatioMetric(name="r", numerator=1, denominator=0).to_record()["value"] is None


# --- gauges -----------------------------------------------------------------


def test_gauge_requires_a_finite_decimal_and_an_aware_instant() -> None:
    gauge = GaugeMetric(name="data.age", value=Decimal("12.5"), observed_at=NOW)
    assert gauge.to_record()["value"] == Decimal("12.5")
    with pytest.raises(MonitoringInputError):
        GaugeMetric(name="g", value=12.5, observed_at=NOW)  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        GaugeMetric(name="g", value=Decimal("nan"), observed_at=NOW)
    with pytest.raises(MonitoringInputError):
        GaugeMetric(name="g", value=Decimal("inf"), observed_at=NOW)
    with pytest.raises(MonitoringInputError):
        GaugeMetric(name="g", value=Decimal(1), observed_at=datetime(2026, 1, 1))  # type: ignore[arg-type]


# --- summary ----------------------------------------------------------------


def test_summary_sorts_by_name_for_determinism() -> None:
    summary = MetricSummary.of(
        CounterMetric(name="z"),
        CounterMetric(name="a"),
        GaugeMetric(name="m", value=Decimal(1), observed_at=NOW),
    )
    assert [entry.name for entry in summary.entries] == ["a", "m", "z"]
    assert summary.get("m") is not None
    assert summary.get("nope") is None


def test_summary_rejects_unsorted_duplicate_or_foreign_entries() -> None:
    with pytest.raises(MonitoringInputError):
        MetricSummary(entries=(CounterMetric(name="b"), CounterMetric(name="a")))
    with pytest.raises(MonitoringInputError):
        MetricSummary(entries=(CounterMetric(name="a"), CounterMetric(name="a")))
    with pytest.raises(MonitoringInputError):
        MetricSummary(entries=[CounterMetric(name="a")])  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        MetricSummary(entries=("a",))  # type: ignore[arg-type]


def test_summary_of_is_deterministic_and_float_free() -> None:
    def build() -> MetricSummary:
        return MetricSummary.of(
            DurationMetric(name="latency").record(timedelta(milliseconds=250)),
            RatioMetric(name="success", numerator=2, denominator=3),
            CounterMetric(name="attempted").increment(2),
        )

    first, second = build(), build()
    assert first == second
    assert evidence_json(first.to_record()) == evidence_json(second.to_record())
    assert _floats_in(first.to_record()) == []


def test_summary_is_immutable() -> None:
    summary = MetricSummary.of(CounterMetric(name="a"))
    with pytest.raises(FrozenInstanceError):
        summary.entries = ()  # type: ignore[misc]
