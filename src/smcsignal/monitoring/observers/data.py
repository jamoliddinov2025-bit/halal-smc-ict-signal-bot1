"""Market-data observation: what arrived, and what it looks like from outside.

This observer reads a published ``OHLCVBatch`` - the frozen candle window plus
the ``ValidationReport`` the cleaning stage already wrote - and turns it into
monitoring values. It is a pure projection: it reads, it never writes back, and
the batch it is given is left exactly as it was found. Nothing here can repair,
reorder, synthesize, replace, or trim a candle; the only thing this module knows
how to do is notice.

Two components are reported. ``DATA_INTEGRITY`` carries conditions about the
payload's own cleanliness - duplicates, dropped rows, trimmed windows, and
reordering - because those are facts the cleaning audit already recorded.
``MARKET_DATA`` carries conditions about the feed itself: whether a window
arrived at all, how old its newest close is, and whether the candle series is
continuous. Failure conditions raised by the data layer are projected the same
way, so a provider outage and a stale series end up on the same component.

Freshness and continuity are computed with the existing frozen helpers rather
than by re-deriving timeframe arithmetic: ``candle_close_time`` says when a
candle actually closed, and ``timeframe_seconds`` says how long one interval is.
A gap is therefore missing *closed intervals*, and a stale series is one whose
newest close is more than ``stale_after_intervals`` interval-durations old. Both
boundaries are strict, so exactly-at-the-threshold is still fresh and still
continuous.

The instant of observation is always supplied by the caller. This module never
reads a clock, and it never derives time from the data it is inspecting: a
window's age is a fact about when the observer looked, not a property of the
candles.

The observer returns a :class:`DataObservation` and touches nothing else. Wiring
the result into a run is the caller's job, because retention, health transitions,
and monitor failure containment all belong to a single authority - the
``MonitoringSession`` - and a second authority here would be able to disagree
with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Final

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.liquidity.time import candle_close_time
from smcsignal.analysis.mtf.timeframes import timeframe_seconds
from smcsignal.data.errors import (
    DataConfigurationError,
    DataProviderError,
    DataValidationError,
    MarketDataError,
    ProviderHTTPError,
    RateLimitError,
)
from smcsignal.data.models import OHLCV, OHLCVBatch, ValidationReport
from smcsignal.monitoring.config import MonitoringConfig
from smcsignal.monitoring.errors import MonitoringInputError
from smcsignal.monitoring.metrics import CounterMetric, DurationMetric, Metric, RatioMetric
from smcsignal.monitoring.models import (
    HealthCode,
    HealthEvent,
    MonitoredComponent,
    build_health_event,
    require_utc_timestamp,
)
from smcsignal.monitoring.monitor import require_metric

_MICROSECOND = timedelta(microseconds=1)
_ZERO = timedelta(0)

# The exact metric names this observer emits. Callers and tests read this tuple
# instead of repeating string literals, so a rename cannot drift.
MARKET_DATA_METRICS: Final[tuple[str, ...]] = (
    "market_data.failures",
    "market_data.freshness_age",
    "market_data.gaps",
    "market_data.output_ratio",
    "market_data.rows.input",
    "market_data.rows.output",
    "market_data.rows.rejected",
)

_REPORT_COUNT_FIELDS: Final[tuple[str, ...]] = (
    "input_rows",
    "output_rows",
    "duplicates_removed",
    "missing_rows_dropped",
    "incomplete_rows_dropped",
    "rows_trimmed",
)

_MAX_SYMBOL_LENGTH: Final[int] = 32
_SYMBOL_CHARACTERS: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _microseconds(value: timedelta) -> int:
    """Return an exact integral microsecond count; never a float."""
    return value // _MICROSECOND


def _detail(**pairs: str) -> tuple[tuple[str, str], ...]:
    """Build a canonical, key-sorted detail tuple."""
    return tuple(sorted(pairs.items()))


def _require_timeframe(value: object) -> str:
    """Validate a timeframe through the existing frozen timeframe helper."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MonitoringInputError("timeframe must be a nonempty, trimmed string")
    try:
        timeframe_seconds(value)
    except AnalysisConfigurationError as exc:
        raise MonitoringInputError(f"unsupported monitoring timeframe: {value}") from exc
    return value


def _require_symbol(value: object) -> str:
    """Validate a series ticker; a symbol becomes part of a stored identity."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MonitoringInputError("symbol must be a nonempty, trimmed string")
    if len(value) > _MAX_SYMBOL_LENGTH or not set(value) <= _SYMBOL_CHARACTERS:
        raise MonitoringInputError(
            "symbol may contain only letters, digits, dot, underscore, hyphen"
        )
    return value


def series_subject(symbol: str, timeframe: str) -> str:
    """Return the canonical subject identity of one observed series."""
    return f"series:{_require_symbol(symbol)}:{_require_timeframe(timeframe)}"


def _require_report(report: object) -> ValidationReport:
    """Validate the published cleaning audit, refusing to interpret nonsense.

    ``ValidationReport`` is a plain frozen record, so a hand-built or corrupted
    instance is possible. The observer validates the invariants it actually
    relies on rather than assuming them; the removal counts are deliberately not
    cross-checked against each other because the producer does not guarantee
    they are disjoint.
    """
    if not isinstance(report, ValidationReport):
        raise MonitoringInputError("report must be a ValidationReport")
    for name in _REPORT_COUNT_FIELDS:
        value = getattr(report, name)
        if type(value) is not int or value < 0:
            raise MonitoringInputError(f"ValidationReport.{name} must be a nonnegative integer")
    if type(report.reordered) is not bool:
        raise MonitoringInputError("ValidationReport.reordered must be a boolean")
    if report.output_rows > report.input_rows:
        raise MonitoringInputError("ValidationReport.output_rows must not exceed input_rows")
    return report


def _require_candles(candles: object, report: ValidationReport) -> tuple[OHLCV, ...]:
    """Validate the published window against its own audit."""
    if not isinstance(candles, tuple):
        raise MonitoringInputError("batch candles must be a tuple")
    for candle in candles:
        if not isinstance(candle, OHLCV):
            raise MonitoringInputError("batch candles must contain only OHLCV records")
    if len(candles) != report.output_rows:
        raise MonitoringInputError("output_rows must equal the number of published candles")
    return candles


@dataclass(frozen=True, slots=True)
class DataObservation:
    """Everything one market-data observation produced.

    Immutable, and deliberately inert: the events and metrics are values to be
    recorded, not instructions to be executed.
    """

    events: tuple[HealthEvent, ...] = ()
    metrics: tuple[Metric, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or not isinstance(self.metrics, tuple):
            raise MonitoringInputError("events and metrics must be tuples")
        for event in self.events:
            if not isinstance(event, HealthEvent):
                raise MonitoringInputError("events must contain only HealthEvent values")
        for metric in self.metrics:
            require_metric(metric)  # raises for anything that is not a metric

    def codes(self) -> tuple[HealthCode, ...]:
        """Return the observed condition codes, in observation order."""
        return tuple(event.code for event in self.events)


@dataclass(frozen=True, slots=True)
class MarketDataObserver:
    """Projects published market-data outcomes into monitoring values.

    The configuration supplies thresholds only; the observer has no monitor, no
    clock, and no producer, so observing cannot alter anything it looks at.
    """

    config: MonitoringConfig = field(default_factory=MonitoringConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.config, MonitoringConfig):
            raise MonitoringInputError("config must be a MonitoringConfig")

    def observe_batch(
        self,
        batch: OHLCVBatch,
        *,
        timeframe: str,
        observed_at: datetime,
        symbol: str | None = None,
    ) -> DataObservation:
        """Observe one published candle window at one instant.

        The event order is fixed: availability, then payload integrity, then
        timeliness, then continuity in series order. Deterministic ordering is
        what lets two identical inputs produce two identical observations.
        """
        if not isinstance(batch, OHLCVBatch):
            raise MonitoringInputError("batch must be an OHLCVBatch")
        require_utc_timestamp(observed_at, "observed_at")
        interval = timedelta(seconds=timeframe_seconds(_require_timeframe(timeframe)))
        report = _require_report(batch.report)
        candles = _require_candles(batch.candles, report)
        subject = series_subject(symbol, timeframe) if symbol is not None else None

        events: list[HealthEvent] = []
        integrity: list[HealthCode] = []

        def observe(code: HealthCode, detail: tuple[tuple[str, str], ...] = ()) -> None:
            events.append(
                build_health_event(
                    component=_component_for(code),
                    code=code,
                    observed_at=observed_at,
                    subject_id=subject,
                    detail=detail,
                )
            )

        if not candles:
            observe(HealthCode.DATA_UNAVAILABLE, _detail(input_rows=str(report.input_rows)))

        for code, removed in (
            (HealthCode.DATA_DUPLICATES_REMOVED, report.duplicates_removed),
            (HealthCode.DATA_MISSING_ROWS_DROPPED, report.missing_rows_dropped),
            (HealthCode.DATA_INCOMPLETE_ROWS_DROPPED, report.incomplete_rows_dropped),
            (HealthCode.DATA_ROWS_TRIMMED, report.rows_trimmed),
        ):
            if removed:
                integrity.append(code)
                observe(code, _detail(rows=str(removed)))
        if report.reordered:
            integrity.append(HealthCode.DATA_REORDERED)
            observe(HealthCode.DATA_REORDERED, _detail(input_rows=str(report.input_rows)))

        age: timedelta | None = None
        if candles:
            newest_close = candle_close_time(candles[-1].timestamp, timeframe)
            age = observed_at - newest_close
            if age < _ZERO:
                observe(
                    HealthCode.DATA_FUTURE_TIMESTAMP,
                    _detail(ahead_microseconds=str(-_microseconds(age))),
                )
            elif _microseconds(age) > _microseconds(interval) * self.config.stale_after_intervals:
                observe(
                    HealthCode.DATA_STALE,
                    _detail(
                        age_intervals=str(_microseconds(age) // _microseconds(interval)),
                        threshold_intervals=str(self.config.stale_after_intervals),
                    ),
                )

        gaps = 0
        for previous, current in pairwise(candles):
            expected_open = candle_close_time(previous.timestamp, timeframe)
            offset = current.timestamp - expected_open
            if offset == _ZERO:
                continue
            if offset < _ZERO:
                observe(
                    HealthCode.DATA_INTERVAL_ANOMALY,
                    _detail(overlap_microseconds=str(-_microseconds(offset))),
                )
                continue
            intervals, remainder = divmod(_microseconds(offset), _microseconds(interval))
            if intervals > self.config.gap_tolerance_intervals:
                gaps += 1
                observe(HealthCode.DATA_GAP, _detail(missing_intervals=str(intervals)))
            if remainder:
                observe(
                    HealthCode.DATA_INTERVAL_ANOMALY,
                    _detail(misaligned_microseconds=str(remainder)),
                )

        metrics: tuple[Metric, ...] = (
            CounterMetric(name="market_data.rows.input", total=report.input_rows),
            CounterMetric(name="market_data.rows.output", total=report.output_rows),
            CounterMetric(
                name="market_data.rows.rejected",
                total=sum(
                    (
                        report.duplicates_removed,
                        report.missing_rows_dropped,
                        report.incomplete_rows_dropped,
                        report.rows_trimmed,
                    )
                ),
            ),
            CounterMetric(name="market_data.gaps", total=gaps),
            RatioMetric(
                name="market_data.output_ratio",
                numerator=report.output_rows,
                denominator=report.input_rows,
            ),
        )
        if age is not None and age >= _ZERO:
            metrics = metrics + (DurationMetric(name="market_data.freshness_age").record(age),)
        return DataObservation(events=tuple(events), metrics=metrics)

    def observe_failure(
        self,
        error: MarketDataError,
        *,
        observed_at: datetime,
        timeframe: str | None = None,
        symbol: str | None = None,
    ) -> DataObservation:
        """Observe one published market-data failure.

        Only the exception's type is projected: the message is never stored,
        because a provider message can embed a request identifier or a URL. An
        exception from outside the frozen market-data hierarchy is refused
        outright rather than being guessed at.
        """
        require_utc_timestamp(observed_at, "observed_at")
        if not isinstance(error, MarketDataError):
            raise MonitoringInputError("error must be a MarketDataError")
        if (symbol is None) != (timeframe is None):
            raise MonitoringInputError("symbol and timeframe must be supplied together")
        subject = series_subject(symbol, timeframe) if symbol is not None and timeframe else None

        code, detail = _classify_failure(error)
        event = build_health_event(
            component=_component_for(code),
            code=code,
            observed_at=observed_at,
            subject_id=subject,
            detail=detail,
        )
        return DataObservation(
            events=(event,),
            metrics=(CounterMetric(name="market_data.failures", total=1),),
        )


def _classify_failure(error: MarketDataError) -> tuple[HealthCode, tuple[tuple[str, str], ...]]:
    """Map a published failure onto its condition code, most specific first."""
    if isinstance(error, RateLimitError):
        return HealthCode.DATA_RATE_LIMITED, _detail(
            error_type=type(error).__name__,
            retry_after_present="true" if error.retry_after is not None else "false",
            status_code=str(error.status_code),
        )
    if isinstance(error, ProviderHTTPError):
        return HealthCode.DATA_PROVIDER_HTTP_ERROR, _detail(
            error_type=type(error).__name__, status_code=str(error.status_code)
        )
    if isinstance(error, DataValidationError):
        return HealthCode.DATA_MALFORMED, _detail(error_type=type(error).__name__)
    if isinstance(error, DataConfigurationError):
        return HealthCode.DATA_UNAVAILABLE, _detail(error_type=type(error).__name__)
    if isinstance(error, DataProviderError):
        return HealthCode.DATA_FETCH_FAILED, _detail(error_type=type(error).__name__)
    return HealthCode.DATA_FETCH_FAILED, _detail(error_type=type(error).__name__)


def _component_for(code: HealthCode) -> MonitoredComponent:
    """Attribute a condition to the component that owns it."""
    if code in (
        HealthCode.DATA_DUPLICATES_REMOVED,
        HealthCode.DATA_MISSING_ROWS_DROPPED,
        HealthCode.DATA_INCOMPLETE_ROWS_DROPPED,
        HealthCode.DATA_ROWS_TRIMMED,
        HealthCode.DATA_REORDERED,
        HealthCode.DATA_MALFORMED,
    ):
        return MonitoredComponent.DATA_INTEGRITY
    return MonitoredComponent.MARKET_DATA
