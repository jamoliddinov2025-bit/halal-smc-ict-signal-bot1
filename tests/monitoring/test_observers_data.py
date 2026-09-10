"""Phase 25B-4 tests: what the market-data observer notices, and what it refuses.

The observer is a pure projection of a published batch and of published failures,
so almost everything here is expressed as "given this frozen input, these are the
exact conditions and these are the exact numbers - and this is what the input
looked like afterwards".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from smcsignal.data.errors import (
    DataConfigurationError,
    DataProviderError,
    DataValidationError,
    MarketDataError,
    ProviderHTTPError,
    RateLimitError,
)
from smcsignal.data.models import OHLCV, OHLCVBatch, ValidationReport
from smcsignal.monitoring import (
    MARKET_DATA_METRICS,
    CounterMetric,
    DataObservation,
    HealthCode,
    MarketDataObserver,
    MonitoredComponent,
    MonitoringConfig,
    MonitoringInputError,
    RatioMetric,
    canonical_record,
    require_serializable,
    series_subject,
)

BASE = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
MISS = timedelta(milliseconds=1)
TIMEFRAME = "1h"


def _candle(open_time: datetime, price: str = "100") -> OHLCV:
    value = Decimal(price)
    return OHLCV(
        timestamp=open_time,
        open=value,
        high=value + 1,
        low=value - 1,
        close=value,
        volume=Decimal("10"),
    )


def _batch(
    open_times: tuple[datetime, ...] = (BASE, BASE + HOUR, BASE + 2 * HOUR),
    *,
    report: ValidationReport | None = None,
) -> OHLCVBatch:
    candles = tuple(_candle(moment) for moment in open_times)
    return OHLCVBatch(
        candles=candles,
        report=report
        if report is not None
        else ValidationReport(input_rows=len(candles), output_rows=len(candles)),
    )


def _observe(
    batch: OHLCVBatch | None = None,
    *,
    observed_at: datetime | None = None,
    config: MonitoringConfig | None = None,
    symbol: str | None = None,
    timeframe: str = TIMEFRAME,
) -> DataObservation:
    observer = MarketDataObserver(config=config or MonitoringConfig())
    return observer.observe_batch(
        batch if batch is not None else _batch(),
        timeframe=timeframe,
        observed_at=observed_at if observed_at is not None else BASE + 3 * HOUR,
        symbol=symbol,
    )


def _detail(observation: DataObservation, code: HealthCode) -> dict[str, str]:
    for event in observation.events:
        if event.code is code:
            return dict(event.detail)
    raise AssertionError(f"{code} was not observed")


def _codes(observation: DataObservation) -> list[HealthCode]:
    return list(observation.codes())


# --- the clean baseline -----------------------------------------------------


def test_a_clean_fresh_contiguous_window_observes_nothing() -> None:
    observation = _observe()
    assert observation.events == ()
    assert [metric.name for metric in observation.metrics] == [
        "market_data.rows.input",
        "market_data.rows.output",
        "market_data.rows.rejected",
        "market_data.gaps",
        "market_data.output_ratio",
        "market_data.freshness_age",
    ]


def test_counts_and_ratio_reflect_the_published_audit() -> None:
    batch = _batch(report=ValidationReport(input_rows=10, output_rows=3, duplicates_removed=7))
    observation = _observe(batch)
    values = {metric.name: metric for metric in observation.metrics}
    assert values["market_data.rows.input"] == CounterMetric(
        name="market_data.rows.input", total=10
    )
    assert values["market_data.rows.output"] == CounterMetric(
        name="market_data.rows.output", total=3
    )
    assert values["market_data.rows.rejected"] == CounterMetric(
        name="market_data.rows.rejected", total=7
    )
    assert isinstance(values["market_data.output_ratio"], RatioMetric)
    assert values["market_data.output_ratio"].value == Decimal(3) / Decimal(10)


def test_the_metric_name_tuple_matches_what_is_emitted() -> None:
    emitted = {metric.name for metric in _observe().metrics}
    assert emitted <= set(MARKET_DATA_METRICS)
    assert set(MARKET_DATA_METRICS) == {
        "market_data.failures",
        "market_data.freshness_age",
        "market_data.gaps",
        "market_data.output_ratio",
        "market_data.rows.input",
        "market_data.rows.output",
        "market_data.rows.rejected",
    }


def test_every_emitted_value_is_float_free_and_serializable() -> None:
    batch = _batch(
        report=ValidationReport(
            input_rows=9,
            output_rows=3,
            duplicates_removed=2,
            missing_rows_dropped=1,
            incomplete_rows_dropped=1,
            rows_trimmed=2,
            reordered=True,
        )
    )
    observation = _observe(batch, observed_at=BASE + 8 * HOUR)
    # require_serializable refuses float, timedelta, naive datetime, and
    # non-finite Decimal, so passing proves the observation carries none.
    require_serializable([event.to_record() for event in observation.events])
    require_serializable([metric.to_record() for metric in observation.metrics])
    assert isinstance(canonical_record([metric.to_record() for metric in observation.metrics]), str)


# --- ValidationReport field by field ----------------------------------------


def test_duplicates_removed_is_observed_at_info_severity() -> None:
    observation = _observe(
        _batch(report=ValidationReport(input_rows=4, output_rows=3, duplicates_removed=1))
    )
    assert _codes(observation) == [HealthCode.DATA_DUPLICATES_REMOVED]
    event = observation.events[0]
    assert event.component is MonitoredComponent.DATA_INTEGRITY
    assert event.severity.value == "info"
    assert _detail(observation, HealthCode.DATA_DUPLICATES_REMOVED) == {"rows": "1"}


def test_missing_rows_dropped_is_observed_as_a_warning() -> None:
    observation = _observe(
        _batch(report=ValidationReport(input_rows=4, output_rows=3, missing_rows_dropped=2))
    )
    assert _codes(observation) == [HealthCode.DATA_MISSING_ROWS_DROPPED]
    assert observation.events[0].severity.value == "warning"
    assert _detail(observation, HealthCode.DATA_MISSING_ROWS_DROPPED) == {"rows": "2"}


def test_incomplete_rows_dropped_is_observed_as_a_warning() -> None:
    observation = _observe(
        _batch(report=ValidationReport(input_rows=4, output_rows=3, incomplete_rows_dropped=3))
    )
    assert _codes(observation) == [HealthCode.DATA_INCOMPLETE_ROWS_DROPPED]
    assert _detail(observation, HealthCode.DATA_INCOMPLETE_ROWS_DROPPED) == {"rows": "3"}


def test_rows_trimmed_is_observed_at_info_severity() -> None:
    observation = _observe(
        _batch(report=ValidationReport(input_rows=9, output_rows=3, rows_trimmed=6))
    )
    assert _codes(observation) == [HealthCode.DATA_ROWS_TRIMMED]
    assert observation.events[0].severity.value == "info"
    assert _detail(observation, HealthCode.DATA_ROWS_TRIMMED) == {"rows": "6"}


def test_reordered_is_observed_as_a_warning() -> None:
    observation = _observe(
        _batch(report=ValidationReport(input_rows=3, output_rows=3, reordered=True))
    )
    assert _codes(observation) == [HealthCode.DATA_REORDERED]
    assert observation.events[0].severity.value == "warning"
    assert _detail(observation, HealthCode.DATA_REORDERED) == {"input_rows": "3"}


def test_reordered_false_is_not_observed() -> None:
    assert _codes(_observe()) == []


def test_every_integrity_field_is_observed_in_a_fixed_order() -> None:
    observation = _observe(
        _batch(
            report=ValidationReport(
                input_rows=20,
                output_rows=3,
                duplicates_removed=2,
                missing_rows_dropped=3,
                incomplete_rows_dropped=4,
                rows_trimmed=5,
                reordered=True,
            )
        )
    )
    assert _codes(observation) == [
        HealthCode.DATA_DUPLICATES_REMOVED,
        HealthCode.DATA_MISSING_ROWS_DROPPED,
        HealthCode.DATA_INCOMPLETE_ROWS_DROPPED,
        HealthCode.DATA_ROWS_TRIMMED,
        HealthCode.DATA_REORDERED,
    ]
    rejected = next(
        metric for metric in observation.metrics if metric.name == "market_data.rows.rejected"
    )
    assert rejected.total == 14


def test_integrity_conditions_are_attributed_to_data_integrity() -> None:
    observation = _observe(
        _batch(
            report=ValidationReport(
                input_rows=9,
                output_rows=3,
                duplicates_removed=1,
                missing_rows_dropped=1,
                incomplete_rows_dropped=1,
                rows_trimmed=3,
                reordered=True,
            )
        )
    )
    assert {event.component for event in observation.events} == {MonitoredComponent.DATA_INTEGRITY}


# --- empty batches ----------------------------------------------------------


def test_an_empty_batch_is_unavailable() -> None:
    observation = _observe(_batch((), report=ValidationReport(input_rows=0, output_rows=0)))
    assert _codes(observation) == [HealthCode.DATA_UNAVAILABLE]
    event = observation.events[0]
    assert event.component is MonitoredComponent.MARKET_DATA
    assert event.severity.value == "critical"
    assert dict(event.detail) == {"input_rows": "0"}


def test_an_empty_window_is_unavailable_even_when_rows_were_dropped() -> None:
    observation = _observe(
        _batch((), report=ValidationReport(input_rows=5, output_rows=0, missing_rows_dropped=5))
    )
    assert _codes(observation) == [
        HealthCode.DATA_UNAVAILABLE,
        HealthCode.DATA_MISSING_ROWS_DROPPED,
    ]


def test_an_empty_batch_reports_no_freshness_and_no_gaps() -> None:
    observation = _observe(_batch((), report=ValidationReport(input_rows=0, output_rows=0)))
    names = {metric.name for metric in observation.metrics}
    assert "market_data.freshness_age" not in names
    gaps = next(metric for metric in observation.metrics if metric.name == "market_data.gaps")
    assert gaps.total == 0
    ratio = next(
        metric for metric in observation.metrics if metric.name == "market_data.output_ratio"
    )
    assert ratio.value is None  # nothing was offered, so nothing is claimed


def test_a_single_candle_window_has_no_continuity_to_judge() -> None:
    observation = _observe(_batch((BASE,)), observed_at=BASE + HOUR)
    assert observation.events == ()
    assert any(metric.name == "market_data.freshness_age" for metric in observation.metrics)


# --- freshness --------------------------------------------------------------


def test_freshness_age_is_measured_from_the_newest_close() -> None:
    observation = _observe(observed_at=BASE + 3 * HOUR + timedelta(minutes=30))
    age = next(
        metric for metric in observation.metrics if metric.name == "market_data.freshness_age"
    )
    assert age.count == 1
    assert age.total == timedelta(minutes=30)
    assert _codes(observation) == []


def test_freshness_exactly_at_the_threshold_is_still_fresh() -> None:
    observation = _observe(observed_at=BASE + 6 * HOUR)  # three full intervals old
    assert HealthCode.DATA_STALE not in _codes(observation)


def test_freshness_one_millisecond_past_the_threshold_is_stale() -> None:
    observation = _observe(observed_at=BASE + 6 * HOUR + MISS)
    assert _codes(observation) == [HealthCode.DATA_STALE]
    assert _detail(observation, HealthCode.DATA_STALE) == {
        "age_intervals": "3",
        "threshold_intervals": "3",
    }


def test_the_stale_threshold_is_configurable() -> None:
    config = MonitoringConfig(stale_after_intervals=4)
    observation = _observe(observed_at=BASE + 7 * HOUR, config=config)  # four intervals old
    assert HealthCode.DATA_STALE not in _codes(observation)
    later = _observe(observed_at=BASE + 7 * HOUR + MISS, config=config)
    assert HealthCode.DATA_STALE in _codes(later)


def test_a_candle_closing_after_the_observation_instant_is_a_future_timestamp() -> None:
    observation = _observe(observed_at=BASE + 2 * HOUR)
    assert _codes(observation) == [HealthCode.DATA_FUTURE_TIMESTAMP]
    event = observation.events[0]
    assert event.severity.value == "critical"
    assert event.component is MonitoredComponent.MARKET_DATA
    assert _detail(observation, HealthCode.DATA_FUTURE_TIMESTAMP) == {
        "ahead_microseconds": "3600000000"
    }


def test_a_future_closing_candle_reports_no_freshness_age() -> None:
    observation = _observe(observed_at=BASE + 2 * HOUR)
    assert all(metric.name != "market_data.freshness_age" for metric in observation.metrics)


def test_freshness_is_measured_against_the_last_candle_not_the_first() -> None:
    observation = _observe(_batch((BASE, BASE + HOUR)), observed_at=BASE + 2 * HOUR)
    assert _codes(observation) == []
    age = next(
        metric for metric in observation.metrics if metric.name == "market_data.freshness_age"
    )
    assert age.total == timedelta(0)


# --- gaps and continuity ----------------------------------------------------


def test_a_contiguous_series_has_no_gaps() -> None:
    observation = _observe()
    assert _codes(observation) == []
    assert next(m for m in observation.metrics if m.name == "market_data.gaps").total == 0


def test_one_missing_candle_is_a_gap() -> None:
    observation = _observe(_batch((BASE, BASE + 2 * HOUR)), observed_at=BASE + 3 * HOUR)
    assert _codes(observation) == [HealthCode.DATA_GAP]
    assert _detail(observation, HealthCode.DATA_GAP) == {"missing_intervals": "1"}
    assert next(m for m in observation.metrics if m.name == "market_data.gaps").total == 1


def test_a_multi_interval_gap_reports_its_size() -> None:
    observation = _observe(_batch((BASE, BASE + 5 * HOUR)), observed_at=BASE + 6 * HOUR)
    assert _detail(observation, HealthCode.DATA_GAP) == {"missing_intervals": "4"}


def test_a_gap_within_tolerance_is_not_reported() -> None:
    observation = _observe(
        _batch((BASE, BASE + 2 * HOUR)),
        observed_at=BASE + 3 * HOUR,
        config=MonitoringConfig(gap_tolerance_intervals=1),
    )
    assert HealthCode.DATA_GAP not in _codes(observation)


def test_a_gap_one_interval_past_tolerance_is_reported() -> None:
    observation = _observe(
        _batch((BASE, BASE + 3 * HOUR)),
        observed_at=BASE + 4 * HOUR,
        config=MonitoringConfig(gap_tolerance_intervals=1),
    )
    assert _detail(observation, HealthCode.DATA_GAP) == {"missing_intervals": "2"}


def test_a_sub_interval_misalignment_is_an_interval_anomaly_not_a_gap() -> None:
    observation = _observe(
        _batch((BASE, BASE + HOUR + timedelta(minutes=30))),
        observed_at=BASE + 2 * HOUR + timedelta(minutes=30),
    )
    assert _codes(observation) == [HealthCode.DATA_INTERVAL_ANOMALY]
    assert _detail(observation, HealthCode.DATA_INTERVAL_ANOMALY) == {
        "misaligned_microseconds": "1800000000"
    }


def test_a_misaligned_gap_is_both_a_gap_and_an_anomaly() -> None:
    observation = _observe(
        _batch((BASE, BASE + 2 * HOUR + timedelta(minutes=30))),
        observed_at=BASE + 3 * HOUR + timedelta(minutes=30),
    )
    assert _codes(observation) == [HealthCode.DATA_GAP, HealthCode.DATA_INTERVAL_ANOMALY]
    # One full interval is missing and the next candle opens half an interval late.
    assert _detail(observation, HealthCode.DATA_GAP) == {"missing_intervals": "1"}
    assert _detail(observation, HealthCode.DATA_INTERVAL_ANOMALY) == {
        "misaligned_microseconds": "1800000000"
    }


def test_an_overlapping_candle_is_an_interval_anomaly() -> None:
    observation = _observe(
        _batch((BASE, BASE + timedelta(minutes=30))),
        observed_at=BASE + HOUR + timedelta(minutes=30),
    )
    assert _codes(observation) == [HealthCode.DATA_INTERVAL_ANOMALY]
    assert _detail(observation, HealthCode.DATA_INTERVAL_ANOMALY) == {
        "overlap_microseconds": "1800000000"
    }


def test_a_reversed_series_never_reports_a_gap() -> None:
    observation = _observe(
        _batch((BASE + 2 * HOUR, BASE)),
        observed_at=BASE + 3 * HOUR,
        config=MonitoringConfig(gap_tolerance_intervals=10),
    )
    assert HealthCode.DATA_GAP not in _codes(observation)


def test_each_gap_is_reported_separately_and_counted_once() -> None:
    observation = _observe(
        _batch((BASE, BASE + 2 * HOUR, BASE + 3 * HOUR, BASE + 6 * HOUR)),
        observed_at=BASE + 7 * HOUR,
    )
    gaps = [event for event in observation.events if event.code is HealthCode.DATA_GAP]
    assert len(gaps) == 2
    assert next(m for m in observation.metrics if m.name == "market_data.gaps").total == 2


def test_gap_sizes_collapse_into_one_aggregate_when_they_match() -> None:
    observation = _observe(
        _batch((BASE, BASE + 2 * HOUR, BASE + 3 * HOUR, BASE + 5 * HOUR)),
        observed_at=BASE + 6 * HOUR,
    )
    gaps = [event for event in observation.events if event.code is HealthCode.DATA_GAP]
    assert len(gaps) == 2
    assert gaps[0].event_id == gaps[1].event_id  # same condition, so it deduplicates


# --- refusals ---------------------------------------------------------------


def test_the_observer_refuses_a_batch_that_is_not_a_batch() -> None:
    observer = MarketDataObserver()
    with pytest.raises(MonitoringInputError):
        observer.observe_batch(_batch().candles, timeframe=TIMEFRAME, observed_at=BASE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "report",
    [
        ValidationReport(input_rows=3, output_rows=4),
        ValidationReport(input_rows=3, output_rows=2),
        ValidationReport(input_rows=-1, output_rows=0),
        ValidationReport(input_rows=3, output_rows=3, duplicates_removed=-1),
        ValidationReport(input_rows=3, output_rows=3, rows_trimmed=None),  # type: ignore[arg-type]
        ValidationReport(input_rows=3, output_rows=3, reordered=1),  # type: ignore[arg-type]
    ],
)
def test_the_observer_refuses_an_inconsistent_published_audit(report: ValidationReport) -> None:
    batch = _batch(report=report)
    with pytest.raises(MonitoringInputError):
        _observe(batch)


def test_the_observer_refuses_a_report_that_is_not_a_report() -> None:
    with pytest.raises(MonitoringInputError):
        _observe(OHLCVBatch(candles=_batch().candles, report="report"))  # type: ignore[arg-type]


def test_the_observer_refuses_candles_that_are_not_candles() -> None:
    batch = OHLCVBatch(candles=("candle",), report=ValidationReport(input_rows=1, output_rows=1))  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        _observe(batch)


def test_the_observer_refuses_a_window_that_disagrees_with_its_own_audit() -> None:
    batch = OHLCVBatch(
        candles=_batch().candles, report=ValidationReport(input_rows=3, output_rows=1)
    )
    with pytest.raises(MonitoringInputError):
        _observe(batch)


def test_the_observer_refuses_a_naive_observation_instant() -> None:
    with pytest.raises(MonitoringInputError):
        _observe(observed_at=datetime(2026, 1, 1, 3, 0))


@pytest.mark.parametrize("timeframe", ["1M", "5x", "", " ", "1 h", "1H"])
def test_the_observer_refuses_an_unsupported_timeframe(timeframe: str) -> None:
    with pytest.raises(MonitoringInputError):
        _observe(timeframe=timeframe)


@pytest.mark.parametrize("symbol", ["", " ", " btcusdt", "btc usdt", "btc/usdt", "x" * 33])
def test_the_observer_refuses_an_unsafe_symbol(symbol: str) -> None:
    with pytest.raises(MonitoringInputError):
        _observe(symbol=symbol)


def test_the_observer_refuses_a_directly_constructed_invalid_observation() -> None:
    with pytest.raises(MonitoringInputError):
        DataObservation(events=("event",))  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        DataObservation(metrics=("metric",))  # type: ignore[arg-type]
    with pytest.raises(MonitoringInputError):
        MarketDataObserver(config="config")  # type: ignore[arg-type]


# --- subjects and series identity ------------------------------------------


def test_a_named_series_carries_a_canonical_subject_and_an_unnamed_one_does_not() -> None:
    named = _observe(symbol="BTCUSDT")
    assert named.events == ()  # a clean window still yields no events
    stale_batch = _batch(report=ValidationReport(input_rows=3, output_rows=3, reordered=True))
    assert _observe(stale_batch, symbol="BTCUSDT").events[0].subject_id == "series:BTCUSDT:1h"
    assert _observe(stale_batch).events[0].subject_id is None


def test_series_subject_is_canonical_and_validated() -> None:
    assert series_subject("BTCUSDT", "1h") == "series:BTCUSDT:1h"
    assert series_subject("ETH-USD", "15m") == "series:ETH-USD:15m"
    for bad_symbol, bad_timeframe in (("", "1h"), ("a b", "1h"), ("BTCUSDT", "1M")):
        with pytest.raises(MonitoringInputError):
            series_subject(bad_symbol, bad_timeframe)


def test_two_series_do_not_share_an_identity() -> None:
    batch = _batch(report=ValidationReport(input_rows=3, output_rows=3, reordered=True))
    first = _observe(batch, symbol="BTCUSDT").events[0]
    second = _observe(batch, symbol="ETHUSDT").events[0]
    assert first.event_id != second.event_id


# --- failure observation ----------------------------------------------------


def _failure(error: MarketDataError, **kwargs: object) -> DataObservation:
    observer = MarketDataObserver()
    return observer.observe_failure(
        error,
        observed_at=kwargs.pop("observed_at", BASE),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_rate_limit_is_mapped_to_its_own_condition() -> None:
    observation = _failure(RateLimitError(429, retry_after="30"))
    assert _codes(observation) == [HealthCode.DATA_RATE_LIMITED]
    assert dict(observation.events[0].detail) == {
        "error_type": "RateLimitError",
        "retry_after_present": "true",
        "status_code": "429",
    }


def test_a_rate_limit_without_a_retry_after_says_so() -> None:
    observation = _failure(RateLimitError(418))
    assert dict(observation.events[0].detail)["retry_after_present"] == "false"


def test_an_http_failure_is_mapped_to_its_own_condition() -> None:
    observation = _failure(ProviderHTTPError(503))
    assert _codes(observation) == [HealthCode.DATA_PROVIDER_HTTP_ERROR]
    assert dict(observation.events[0].detail) == {
        "error_type": "ProviderHTTPError",
        "status_code": "503",
    }


def test_a_validation_failure_is_malformed_and_belongs_to_integrity() -> None:
    observation = _failure(DataValidationError("row 4 is incomplete"))
    assert _codes(observation) == [HealthCode.DATA_MALFORMED]
    assert observation.events[0].component is MonitoredComponent.DATA_INTEGRITY
    assert dict(observation.events[0].detail) == {"error_type": "DataValidationError"}


def test_a_configuration_failure_is_unavailable() -> None:
    observation = _failure(DataConfigurationError("bad key"))
    assert _codes(observation) == [HealthCode.DATA_UNAVAILABLE]


def test_a_provider_failure_is_a_fetch_failure() -> None:
    observation = _failure(DataProviderError("connection reset"))
    assert _codes(observation) == [HealthCode.DATA_FETCH_FAILED]


def test_a_bare_market_data_error_is_conservatively_a_fetch_failure() -> None:
    observation = _failure(MarketDataError("something else"))
    assert _codes(observation) == [HealthCode.DATA_FETCH_FAILED]


def test_a_failure_message_is_never_stored() -> None:
    secret = "https://example.invalid/data?token=abc123"
    observation = _failure(DataProviderError(secret))
    stored = " ".join(value for _, value in observation.events[0].detail)
    assert "abc123" not in stored
    assert "example.invalid" not in stored
    assert "?" not in stored


def test_a_failure_emits_exactly_one_failure_metric() -> None:
    observation = _failure(ProviderHTTPError(500))
    assert observation.metrics == (CounterMetric(name="market_data.failures", total=1),)


def test_a_failure_can_carry_a_series_subject() -> None:
    observation = _failure(ProviderHTTPError(500), symbol="BTCUSDT", timeframe="1h")
    assert observation.events[0].subject_id == "series:BTCUSDT:1h"


def test_a_failure_refuses_half_a_series_identity() -> None:
    with pytest.raises(MonitoringInputError):
        _failure(ProviderHTTPError(500), symbol="BTCUSDT")
    with pytest.raises(MonitoringInputError):
        _failure(ProviderHTTPError(500), timeframe="1h")


def test_a_failure_refuses_an_error_from_outside_the_frozen_hierarchy() -> None:
    with pytest.raises(MonitoringInputError):
        _failure(RuntimeError("not a market-data failure"))  # type: ignore[arg-type]


def test_a_failure_refuses_a_naive_observation_instant() -> None:
    with pytest.raises(MonitoringInputError):
        _failure(ProviderHTTPError(500), observed_at=datetime(2026, 1, 1, 0, 0))


# --- determinism and purity -------------------------------------------------


def test_two_identical_observations_are_equal() -> None:
    first = _observe(symbol="BTCUSDT")
    second = _observe(symbol="BTCUSDT")
    assert first == second
    assert [event.event_id for event in first.events] == [event.event_id for event in second.events]


def test_a_dirty_observation_is_deterministic_too() -> None:
    report = ValidationReport(
        input_rows=20,
        output_rows=4,
        duplicates_removed=1,
        missing_rows_dropped=1,
        incomplete_rows_dropped=1,
        rows_trimmed=2,
        reordered=True,
    )
    batch = _batch(
        (BASE, BASE + 2 * HOUR, BASE + 3 * HOUR + timedelta(minutes=30), BASE + 6 * HOUR),
        report=report,
    )
    first = _observe(batch, observed_at=BASE + 12 * HOUR, symbol="BTCUSDT")
    second = _observe(batch, observed_at=BASE + 12 * HOUR, symbol="BTCUSDT")
    assert first == second
    assert canonical_record([event.to_record() for event in first.events]) == canonical_record(
        [event.to_record() for event in second.events]
    )


def test_observing_never_mutates_the_batch() -> None:
    report = ValidationReport(input_rows=4, output_rows=3, duplicates_removed=1)
    batch = _batch(report=report)
    candles_before = batch.candles
    before = canonical_record(
        batch.report.__dict__
        if hasattr(batch.report, "__dict__")
        else {
            name: getattr(batch.report, name)
            for name in (
                "input_rows",
                "output_rows",
                "duplicates_removed",
                "missing_rows_dropped",
                "incomplete_rows_dropped",
                "rows_trimmed",
                "reordered",
            )
        }
    )
    _observe(batch, observed_at=BASE + 8 * HOUR)
    assert batch.candles is candles_before
    assert batch.report == report
    after = canonical_record(
        {
            name: getattr(batch.report, name)
            for name in (
                "input_rows",
                "output_rows",
                "duplicates_removed",
                "missing_rows_dropped",
                "incomplete_rows_dropped",
                "rows_trimmed",
                "reordered",
            )
        }
    )
    assert after == before
    assert [candle.to_record() for candle in batch.candles] == [
        candle.to_record() for candle in candles_before
    ]


def test_the_observer_holds_no_monitor_and_no_clock() -> None:
    observer = MarketDataObserver()
    assert not hasattr(observer, "__dict__")  # slotted and frozen
    for forbidden in ("monitor", "clock", "session"):
        assert not hasattr(observer, forbidden)


def test_the_observation_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    observation = _observe()
    with pytest.raises(FrozenInstanceError):
        observation.events = ()  # type: ignore[misc]


def test_the_observer_defaults_to_the_inert_configuration() -> None:
    assert MarketDataObserver().config == MonitoringConfig()
    assert MarketDataObserver().config.enabled is False


def test_the_observer_scales_with_the_timeframe_it_is_given() -> None:
    fifteen = datetime(2026, 1, 1, tzinfo=UTC)
    step = timedelta(minutes=15)
    batch = _batch((fifteen, fifteen + step))
    observation = _observe(batch, timeframe="15m", observed_at=fifteen + 2 * step)
    assert _codes(observation) == []
    missing = _observe(
        _batch((fifteen, fifteen + 2 * step)), timeframe="15m", observed_at=fifteen + 3 * step
    )
    assert _detail(missing, HealthCode.DATA_GAP) == {"missing_intervals": "1"}
