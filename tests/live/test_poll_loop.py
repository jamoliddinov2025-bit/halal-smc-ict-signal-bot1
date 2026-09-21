"""Phase 35A poll loop tests: candle-boundary scheduling, jitter, backoff, shutdown.

Every case drives the loop with a fake clock whose ``sleep`` advances time and
records each wait, a scripted ``CycleRunner``, and an injected random source;
no test sleeps for real, spawns a thread, or opens a socket (the pytest
process forbids socket access). Every ``run`` passes a ``max_cycles`` safety
bound and the fake clock always makes progress, so a broken stop condition
fails an assertion instead of hanging the suite.
"""

from __future__ import annotations

import ast
import dataclasses
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import FrameType

import pytest

import smcsignal.live
import smcsignal.live.poll_loop as poll_loop_module
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.data.errors import (
    DataConfigurationError,
    DataProviderError,
    DataValidationError,
    ProviderHTTPError,
    RateLimitError,
)
from smcsignal.live import (
    DEFAULT_POLL_BACKOFF_BASE_SECONDS,
    DEFAULT_POLL_BACKOFF_MAX_SECONDS,
    DEFAULT_POLL_JITTER_MAX_SECONDS,
    DEFAULT_POLL_JITTER_MIN_SECONDS,
    DEFAULT_POLL_WAIT_CHUNK_SECONDS,
    RECOVERABLE_ERRORS,
    CycleReport,
    CycleRunner,
    LiveConfigurationError,
    LiveFeedError,
    LivePollLoop,
    LivePollLoopConfig,
    PollCycleResult,
    PollOutcome,
    classify_failure,
    install_stop_signal_handlers,
    load_live_config,
    load_poll_loop_config,
    next_poll_time,
)
from tests.live.test_service import scripted_service

T0 = datetime(2024, 1, 2, 12, 15, tzinfo=UTC)  # exactly on a 15m boundary
START = T0 + timedelta(seconds=2)  # 12:15:02 → the first 15m boundary is 12:30:00 (898 s)
NEXT = datetime(2024, 1, 2, 12, 30, tzinfo=UTC)
AFTER_NEXT = datetime(2024, 1, 2, 12, 45, tzinfo=UTC)
BIG_CHUNK = 3600.0  # one wait chunk per 15m wait keeps the sleep sequences readable


def at(hour: int, minute: int, second: int = 0, microsecond: int = 0) -> datetime:
    return datetime(2024, 1, 2, hour, minute, second, microsecond, tzinfo=UTC)


def report(frames: int = 0, buy_signals: int = 0) -> CycleReport:
    return CycleReport(
        primary_candles=frames,
        higher_candles=0,
        frames=frames,
        buy_signals=buy_signals,
        delivery_states=(),
        ledger_persisted=frames > 0,
    )


class FakeClock:
    """A controllable UTC clock whose ``sleep`` advances time and records every wait."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start
        self.sleeps: list[float] = []
        self.reads = 0
        self.on_sleep: Callable[[FakeClock], None] | None = None

    def __call__(self) -> datetime:
        self.reads += 1
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0, "the loop never asks for a zero or negative wait"
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)
        if self.on_sleep is not None:
            self.on_sleep(self)


class ScriptedRunner:
    """A ``CycleRunner`` returning or raising one scripted item per cycle (last repeats)."""

    def __init__(
        self,
        script: Sequence[CycleReport | BaseException],
        *,
        clock: FakeClock | None = None,
        duration_seconds: float = 0.0,
        on_run: Callable[[int], None] | None = None,
    ) -> None:
        self.script = list(script) or [report()]
        self.clock = clock
        self.duration_seconds = duration_seconds
        self.on_run = on_run
        self.calls = 0
        self.started_at: list[datetime] = []

    def run_cycle(self) -> CycleReport:
        self.calls += 1
        if self.clock is not None:
            self.started_at.append(self.clock.now)
            if self.duration_seconds:
                self.clock.now += timedelta(seconds=self.duration_seconds)
        if self.on_run is not None:
            self.on_run(self.calls)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def config(
    timeframe: str = "15m",
    *,
    jitter: tuple[float, float] = (0.0, 0.0),
    base: float = 1.0,
    cap: float = 60.0,
    chunk: float = BIG_CHUNK,
    max_failures: int | None = None,
) -> LivePollLoopConfig:
    return LivePollLoopConfig(
        timeframe=timeframe,
        jitter_min_seconds=jitter[0],
        jitter_max_seconds=jitter[1],
        backoff_base_seconds=base,
        backoff_max_seconds=cap,
        max_consecutive_failures=max_failures,
        wait_chunk_seconds=chunk,
    )


def make_loop(
    script: Sequence[CycleReport | BaseException] = (),
    *,
    start: datetime = START,
    settings: LivePollLoopConfig | None = None,
    rng: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    duration_seconds: float = 0.0,
    on_run: Callable[[int], None] | None = None,
    on_cycle: Callable[[PollCycleResult], None] | None = None,
    history_limit: int = 256,
) -> tuple[LivePollLoop, FakeClock, ScriptedRunner]:
    clock = FakeClock(start)
    runner = ScriptedRunner(script, clock=clock, duration_seconds=duration_seconds, on_run=on_run)
    loop = LivePollLoop(
        runner,
        settings if settings is not None else config(),
        clock=clock,
        sleep_fn=sleeper if sleeper is not None else clock.sleep,
        random_source=rng,
        on_cycle=on_cycle,
        history_limit=history_limit,
    )
    return loop, clock, runner


def never_random() -> float:
    raise AssertionError("the random source must not be consulted")


# --------------------------------------------------------------------------
# Boundary scheduling: next_poll_time
# --------------------------------------------------------------------------


def test_exact_boundary_schedules_the_following_boundary_never_now() -> None:
    # The anti-spin rule: 12:15:00 on a 15m grid → 12:30:00, not 12:15:00.
    assert next_poll_time(T0, "15m") == NEXT
    assert next_poll_time(at(12, 0), "15m") == T0
    assert next_poll_time(at(0, 0), "15m") == at(0, 15)


def test_just_after_boundary_schedules_the_next_boundary() -> None:
    assert next_poll_time(T0 + timedelta(microseconds=1), "15m") == NEXT
    assert next_poll_time(START, "15m") == NEXT  # 12:15:02 → 12:30:00


def test_halfway_through_the_candle_schedules_the_next_boundary() -> None:
    assert next_poll_time(at(12, 22, 30), "15m") == NEXT


def test_just_before_the_next_boundary_schedules_that_boundary() -> None:
    assert next_poll_time(at(12, 29, 59, 999999), "15m") == NEXT
    assert next_poll_time(NEXT, "15m") == AFTER_NEXT


@pytest.mark.parametrize(
    ("timeframe", "now", "expected"),
    [
        ("1m", at(12, 15, 30), at(12, 16)),
        ("1m", at(12, 15), at(12, 16)),
        ("3m", at(12, 16), at(12, 18)),
        ("5m", at(12, 17, 59), at(12, 20)),
        ("15m", at(12, 30), at(12, 45)),
        ("30m", at(12, 30), at(13, 0)),
        ("1h", at(12, 15), at(13, 0)),
        ("2h", at(12, 15), at(14, 0)),
        ("4h", at(12, 15), at(16, 0)),
        ("4h", at(16, 0), at(20, 0)),
        ("6h", at(12, 0), at(18, 0)),
        ("12h", at(12, 0), datetime(2024, 1, 3, 0, 0, tzinfo=UTC)),
        ("1d", at(12, 15), datetime(2024, 1, 3, 0, 0, tzinfo=UTC)),
        ("1d", datetime(2024, 1, 3, tzinfo=UTC), datetime(2024, 1, 4, tzinfo=UTC)),
        # 3d candles count from the Unix epoch: 2024-01-03 is a 3d boundary.
        ("3d", at(12, 15), datetime(2024, 1, 3, tzinfo=UTC)),
        # Weekly candles open on Monday: 2024-01-02 is a Tuesday → Monday 2024-01-08.
        ("1w", at(12, 15), datetime(2024, 1, 8, tzinfo=UTC)),
        ("1w", datetime(2024, 1, 8, tzinfo=UTC), datetime(2024, 1, 15, tzinfo=UTC)),
    ],
)
def test_multiple_timeframes_schedule_on_their_own_absolute_grid(
    timeframe: str, now: datetime, expected: datetime
) -> None:
    assert next_poll_time(now, timeframe) == expected
    assert expected.strftime("%A") == "Monday" or timeframe != "1w"


def test_scheduling_is_grid_based_with_no_cumulative_drift() -> None:
    # Simulate 2,000 cycles that each finish a little after their boundary:
    # every next target is an exact multiple of 900 s from the epoch and every
    # step is exactly one interval — nothing accumulates.
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    current = START
    previous_boundary = None
    for step in range(2000):
        boundary = next_poll_time(current, "15m")
        assert (boundary - epoch) % timedelta(minutes=15) == timedelta(0)
        assert boundary > current
        if previous_boundary is not None:
            assert boundary - previous_boundary == timedelta(minutes=15)
        previous_boundary = boundary
        # The cycle "ran" for a varying sub-interval duration, including 0.
        current = boundary + timedelta(seconds=(step * 7) % 600, microseconds=step % 1000)
    assert previous_boundary == START + timedelta(minutes=15 * 2000) - timedelta(seconds=2)


def test_next_poll_time_expresses_the_grid_in_utc_for_any_offset() -> None:
    plus_two = timezone(timedelta(hours=2))
    local = datetime(2024, 1, 2, 14, 15, 2, tzinfo=plus_two)  # == 12:15:02Z
    result = next_poll_time(local, "15m")
    assert result == NEXT
    assert result.tzinfo is UTC


def test_next_poll_time_rejects_naive_times_and_invalid_grids() -> None:
    with pytest.raises(LiveConfigurationError, match="timezone-aware"):
        next_poll_time(datetime(2024, 1, 2, 12, 15), "15m")
    with pytest.raises(LiveConfigurationError, match="invalid"):
        next_poll_time(T0, "7m")
    with pytest.raises(LiveConfigurationError, match="invalid"):
        next_poll_time(T0, "1M")  # not a fixed-duration grid
    with pytest.raises(LiveConfigurationError, match="nonempty"):
        next_poll_time(T0, "")


# --------------------------------------------------------------------------
# Jitter
# --------------------------------------------------------------------------


def test_zero_jitter_polls_exactly_on_the_boundary_without_consulting_the_rng() -> None:
    loop, clock, runner = make_loop([report()], rng=never_random)
    assert loop.run(max_cycles=2) == 2
    assert clock.sleeps == [898.0, 900.0]
    assert runner.started_at == [NEXT, AFTER_NEXT]
    assert [result.scheduled_at for result in loop.history] == [NEXT, AFTER_NEXT]


def test_minimum_jitter_when_the_source_returns_zero() -> None:
    loop, clock, runner = make_loop([report()], settings=config(jitter=(1.0, 5.0)), rng=lambda: 0.0)
    loop.run(max_cycles=1)
    assert clock.sleeps == [899.0]
    assert runner.started_at == [NEXT + timedelta(seconds=1)]


def test_maximum_jitter_when_the_source_returns_one() -> None:
    loop, clock, runner = make_loop([report()], settings=config(jitter=(1.0, 5.0)), rng=lambda: 1.0)
    loop.run(max_cycles=1)
    assert clock.sleeps == [903.0]
    assert runner.started_at == [NEXT + timedelta(seconds=5)]


def test_intermediate_jitter_is_linear_inside_the_band() -> None:
    loop, clock, runner = make_loop(
        [report()], settings=config(jitter=(1.0, 5.0)), rng=lambda: 0.25
    )
    loop.run(max_cycles=1)
    assert clock.sleeps == [900.0]
    assert runner.started_at == [NEXT + timedelta(seconds=2)]


@pytest.mark.parametrize("fraction", [-1.0, -0.001, 0.0, 0.1, 0.5, 0.999, 1.0, 1.5, 7.0])
def test_jitter_never_schedules_before_the_boundary_nor_reaches_the_next(
    fraction: float,
) -> None:
    band = (1.0, 5.0)
    loop, _, runner = make_loop([report()], settings=config(jitter=band), rng=lambda: fraction)
    loop.run(max_cycles=3)
    for started, expected_boundary in zip(
        runner.started_at, (NEXT, AFTER_NEXT, at(13, 0)), strict=True
    ):
        offset = (started - expected_boundary).total_seconds()
        assert band[0] <= offset <= band[1]  # out-of-range sources are clamped, never trusted
        assert started < expected_boundary + timedelta(minutes=15)


def test_a_fixed_band_is_deterministic_without_a_random_draw() -> None:
    loop, clock, _ = make_loop([report()], settings=config(jitter=(2.5, 2.5)), rng=never_random)
    loop.run(max_cycles=2)
    assert clock.sleeps == [900.5, 900.0]


def test_deterministic_injected_rng_reproduces_the_same_schedule() -> None:
    def scripted_rng() -> Callable[[], float]:
        values = iter([0.2, 0.9, 0.4, 0.0])
        return lambda: next(values)

    first, first_clock, _ = make_loop(
        [report()], settings=config(jitter=(0.0, 4.0)), rng=scripted_rng()
    )
    second, second_clock, _ = make_loop(
        [report()], settings=config(jitter=(0.0, 4.0)), rng=scripted_rng()
    )
    first.run(max_cycles=4)
    second.run(max_cycles=4)
    assert first_clock.sleeps == second_clock.sleeps
    assert [r.scheduled_at for r in first.history] == [r.scheduled_at for r in second.history]
    assert first_clock.sleeps == [898.8, 902.8, 898.0, 898.4]  # 0.8, 3.6, 1.6, 0.0 offsets


def test_random_source_must_return_a_finite_number() -> None:
    loop, _, runner = make_loop(
        [report()], settings=config(jitter=(0.0, 4.0)), rng=lambda: float("nan")
    )
    with pytest.raises(LiveConfigurationError, match="finite"):
        loop.run(max_cycles=1)
    assert runner.calls == 0


def test_jitter_band_cannot_reach_the_next_candle() -> None:
    with pytest.raises(LiveConfigurationError, match="smaller than the 1m interval"):
        config("1m", jitter=(0.0, 60.0))
    config("1m", jitter=(0.0, 59.0))  # strictly inside the interval is fine


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


def test_successful_cycle_records_the_report_and_schedules_the_absolute_boundary() -> None:
    delivered = report(frames=3, buy_signals=1)
    loop, clock, runner = make_loop([delivered], duration_seconds=3.0)
    assert loop.run(max_cycles=1) == 1
    (result,) = loop.history
    assert result.sequence == 1
    assert result.outcome is PollOutcome.SUCCESS
    assert result.scheduled_at == NEXT
    assert result.started_at == NEXT
    assert result.finished_at == NEXT + timedelta(seconds=3)
    assert result.duration_seconds == 3.0
    assert result.report is delivered  # the runner's own report, untouched
    assert result.error is None
    assert result.consecutive_failures == 0
    assert result.next_poll_at is None  # max_cycles reached: this run polls no more
    assert loop.next_poll_at is None
    assert loop.cycles == 1 and runner.calls == 1
    assert clock.sleeps == [898.0]


def test_next_poll_after_success_is_the_boundary_after_the_cycle_finished() -> None:
    loop, clock, runner = make_loop([report()], duration_seconds=3.0)
    loop.run(max_cycles=3)
    # Cycle 1 ends 12:30:03 → 12:45:00 (897 s), cycle 2 ends 12:45:03 → 13:00:00.
    assert clock.sleeps == [898.0, 897.0, 897.0]
    assert runner.started_at == [NEXT, AFTER_NEXT, at(13, 0)]
    assert [r.next_poll_at for r in loop.history] == [AFTER_NEXT, at(13, 0), None]


def test_success_resets_the_failure_counter_and_returns_to_the_grid() -> None:
    loop, clock, _ = make_loop([LiveFeedError("down"), LiveFeedError("down"), report()])
    loop.run(max_cycles=4)
    outcomes = [r.outcome for r in loop.history]
    assert outcomes == [
        PollOutcome.RECOVERABLE_FAILURE,
        PollOutcome.RECOVERABLE_FAILURE,
        PollOutcome.SUCCESS,
        PollOutcome.SUCCESS,
    ]
    assert [r.consecutive_failures for r in loop.history] == [1, 2, 0, 0]
    # 898 to the boundary, 1 s and 2 s of backoff, then back on the grid:
    # the success at 12:30:03 schedules 12:45:00 (897 s), not "now + interval".
    assert clock.sleeps == [898.0, 1.0, 2.0, 897.0]
    assert loop.consecutive_failures == 0


def test_a_cycle_that_outlives_its_candle_resumes_on_the_next_absolute_boundary() -> None:
    loop, clock, runner = make_loop([report()], duration_seconds=20 * 60)
    loop.run(max_cycles=2)
    # 12:30:00 + 20 min = 12:50:00 → the next boundary strictly after is 13:00:00.
    assert runner.started_at == [NEXT, at(13, 0)]
    assert clock.sleeps == [898.0, 600.0]


# --------------------------------------------------------------------------
# Recoverable failures and backoff
# --------------------------------------------------------------------------


def test_live_feed_error_is_recoverable_with_exponential_backoff() -> None:
    failures: list[BaseException] = [LiveFeedError(f"outage {n}") for n in range(4)]
    loop, clock, runner = make_loop([*failures, report()])
    assert loop.run(max_cycles=5) == 5
    assert clock.sleeps == [898.0, 1.0, 2.0, 4.0, 8.0]
    assert [r.outcome for r in loop.history[:4]] == [PollOutcome.RECOVERABLE_FAILURE] * 4
    assert loop.history[0].error == "LiveFeedError: outage 0"
    assert loop.history[0].report is None
    assert [r.consecutive_failures for r in loop.history] == [1, 2, 3, 4, 0]
    assert loop.history[0].next_poll_at == NEXT + timedelta(seconds=1)
    assert loop.history[3].next_poll_at == NEXT + timedelta(seconds=15)
    assert runner.calls == 5


@pytest.mark.parametrize(
    ("error", "expected_backoff"),
    [
        (DataProviderError("transport failed"), 1.0),
        (ProviderHTTPError(503), 1.0),
        (
            RateLimitError(429, "3"),
            3.0,
        ),  # Phase 35C: Retry-After respected → max(backoff, retry_after)
    ],
)
def test_data_provider_errors_are_recoverable(error: Exception, expected_backoff: float) -> None:
    loop, clock, _ = make_loop([error, report()])
    loop.run(max_cycles=2)
    assert loop.history[0].outcome is PollOutcome.RECOVERABLE_FAILURE
    assert loop.history[1].outcome is PollOutcome.SUCCESS
    assert clock.sleeps == [898.0, expected_backoff]


def test_data_validation_error_is_recoverable_at_the_loop_layer() -> None:
    # A malformed/partial upstream page is an operational condition of the
    # moment, not an immutable configuration or state failure: retry later.
    loop, clock, _ = make_loop(
        [DataValidationError("Binance row 3: expected a 12-field kline"), report()]
    )
    loop.run(max_cycles=2)
    assert loop.history[0].outcome is PollOutcome.RECOVERABLE_FAILURE
    assert loop.history[0].error.startswith("DataValidationError: ")  # type: ignore[union-attr]
    assert loop.history[1].outcome is PollOutcome.SUCCESS
    assert clock.sleeps == [898.0, 1.0]


def test_backoff_is_capped() -> None:
    loop, clock, _ = make_loop([LiveFeedError("down")], settings=config(cap=8.0))
    loop.run(max_cycles=7)
    assert clock.sleeps == [898.0, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_backoff_uses_the_configured_base() -> None:
    loop, clock, _ = make_loop([LiveFeedError("down")], settings=config(base=0.5, cap=30.0))
    loop.run(max_cycles=5)
    assert clock.sleeps == [898.0, 0.5, 1.0, 2.0, 4.0]


def test_default_backoff_policy_is_one_second_base_doubling_to_a_minute() -> None:
    settings = LivePollLoopConfig("15m")
    assert settings.backoff_base_seconds == DEFAULT_POLL_BACKOFF_BASE_SECONDS == 1.0
    assert settings.backoff_max_seconds == DEFAULT_POLL_BACKOFF_MAX_SECONDS == 60.0
    assert [settings.backoff_seconds(n) for n in range(1, 9)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        16.0,
        32.0,
        60.0,
        60.0,
    ]
    assert settings.backoff_seconds(10_000) == 60.0  # no overflow for a very long outage
    with pytest.raises(LiveConfigurationError):
        settings.backoff_seconds(0)


def test_backoff_retry_is_not_delayed_to_the_next_boundary() -> None:
    # Recovery happens on the backoff clock: the retry after a failure at
    # 12:30:00 runs at 12:30:01, not at 12:45:00.
    loop, _, runner = make_loop([LiveFeedError("down"), report()])
    loop.run(max_cycles=2)
    assert runner.started_at == [NEXT, NEXT + timedelta(seconds=1)]


def test_consecutive_failure_budget_turns_a_persistent_outage_into_a_fatal_stop() -> None:
    # A persistent outage: every cycle fails. The budget is the bound.
    loop, clock, runner = make_loop([LiveFeedError("down")], settings=config(max_failures=3))
    with pytest.raises(LiveFeedError, match="down"):
        loop.run(max_cycles=10)
    assert runner.calls == 3
    assert clock.sleeps == [898.0, 1.0, 2.0]  # no wait is scheduled after the fatal stop
    assert [r.outcome for r in loop.history] == [
        PollOutcome.RECOVERABLE_FAILURE,
        PollOutcome.RECOVERABLE_FAILURE,
        PollOutcome.FATAL_FAILURE,
    ]
    last = loop.history[-1]
    assert last.error == (
        "retry budget exhausted after 3 consecutive recoverable failures: LiveFeedError: down"
    )
    assert last.next_poll_at is None
    assert loop.next_poll_at is None


def test_classify_failure_table() -> None:
    recoverable: tuple[Exception, ...] = (
        LiveFeedError("x"),
        DataProviderError("x"),
        DataValidationError("x"),
    )
    fatal: tuple[Exception, ...] = (
        LiveConfigurationError("x"),
        AnalysisInputError("x"),
        AnalysisConfigurationError("x"),
        DataConfigurationError("x"),
        ValueError("plain"),
        RuntimeError("plain"),
        OSError("disk"),
        KeyError("k"),
    )
    for error in recoverable:
        assert classify_failure(error) is PollOutcome.RECOVERABLE_FAILURE
    for error in fatal:
        assert classify_failure(error) is PollOutcome.FATAL_FAILURE
    assert RECOVERABLE_ERRORS == (LiveFeedError, DataProviderError, DataValidationError)


# --------------------------------------------------------------------------
# Fatal failures
# --------------------------------------------------------------------------


def test_configuration_failure_is_fatal_recorded_and_surfaced() -> None:
    error = LiveConfigurationError("the persisted live window belongs to a different series")
    loop, clock, runner = make_loop([error, report()])
    with pytest.raises(LiveConfigurationError) as raised:
        loop.run(max_cycles=5)
    assert raised.value is error  # the very exception, not a wrapper
    assert runner.calls == 1  # no retry
    assert clock.sleeps == [898.0]  # no backoff wait after a fatal failure
    (result,) = loop.history
    assert result.outcome is PollOutcome.FATAL_FAILURE
    assert result.error == "LiveConfigurationError: " + str(error)
    assert result.consecutive_failures == 0
    assert result.next_poll_at is None
    assert loop.running is False


@pytest.mark.parametrize(
    "error",
    [
        AnalysisInputError("the feed reported an untracked higher timeframe: 2h"),
        AnalysisConfigurationError("unsupported timeframe"),
        DataConfigurationError("bad config"),
        ValueError("unclassified value error"),
        RuntimeError("unclassified runtime error"),
        OSError("ledger store write failed"),
        TypeError("programming error"),
    ],
)
def test_state_contract_and_unclassified_failures_stop_the_loop(error: Exception) -> None:
    loop, clock, runner = make_loop([error, report()])
    with pytest.raises(type(error)):
        loop.run(max_cycles=5)
    assert runner.calls == 1
    assert clock.sleeps == [898.0]
    assert loop.history[-1].outcome is PollOutcome.FATAL_FAILURE


def test_fatal_failure_never_enters_an_infinite_retry() -> None:
    attempts: list[int] = []
    loop, _, _ = make_loop(
        [AnalysisInputError("state broken")],
        on_run=attempts.append,
    )
    with pytest.raises(AnalysisInputError):
        loop.run()  # deliberately unbounded: the fatal stop is the bound
    assert attempts == [1]


def test_fatal_result_reaches_the_observer_before_the_exception_surfaces() -> None:
    seen: list[PollCycleResult] = []
    loop, _, _ = make_loop([RuntimeError("boom")], on_cycle=seen.append)
    with pytest.raises(RuntimeError):
        loop.run(max_cycles=1)
    assert [r.outcome for r in seen] == [PollOutcome.FATAL_FAILURE]
    assert seen[0].error == "RuntimeError: boom"


# --------------------------------------------------------------------------
# Shutdown
# --------------------------------------------------------------------------


def test_stop_before_the_first_cycle_runs_nothing_and_waits_for_nothing() -> None:
    loop, clock, runner = make_loop([report()])
    loop.request_stop("operator")
    assert loop.run(max_cycles=3) == 0
    assert runner.calls == 0
    assert clock.sleeps == []
    assert loop.history == ()
    assert loop.stop_requested is True
    assert loop.stop_reason == "operator"


def test_stop_while_waiting_for_the_boundary_exits_without_polling() -> None:
    loop, clock, runner = make_loop([report()], settings=config(chunk=60.0))

    def stop_at_twenty_past(current: FakeClock) -> None:
        if current.now >= at(12, 20):
            loop.request_stop("signal SIGTERM")

    clock.on_sleep = stop_at_twenty_past
    assert loop.run(max_cycles=3) == 0
    # 12:15:02 → five 60 s chunks reach 12:20:02, the stop is seen before the sixth.
    assert clock.sleeps == [60.0] * 5
    assert runner.calls == 0
    assert loop.stop_reason == "signal SIGTERM"


def test_waits_are_chunked_so_a_stop_is_noticed_within_one_chunk() -> None:
    loop, clock, _ = make_loop([report()], settings=config(chunk=1.0))
    loop.run(max_cycles=1)
    assert len(clock.sleeps) == 898
    assert set(clock.sleeps) == {1.0}
    assert sum(clock.sleeps) == 898.0


def test_stop_during_a_cycle_finishes_that_cycle_then_exits_cleanly() -> None:
    # A signal arriving mid-cycle only sets the flag; the cycle's ledger and
    # delivery work completes, and no further cycle is scheduled or waited for.
    delivered = report(frames=1, buy_signals=1)
    loop, clock, runner = make_loop([delivered], duration_seconds=2.0)
    runner.on_run = lambda _: loop.request_stop("signal SIGINT")
    assert loop.run(max_cycles=5) == 1
    assert runner.calls == 1
    assert clock.sleeps == [898.0]
    (result,) = loop.history
    assert result.outcome is PollOutcome.SUCCESS
    assert result.report is delivered
    assert result.finished_at == NEXT + timedelta(seconds=2)
    assert result.next_poll_at is None
    assert loop.next_poll_at is None
    assert loop.running is False


def test_stop_during_backoff_exits_without_retrying() -> None:
    loop, clock, runner = make_loop([LiveFeedError("down"), report()], settings=config(base=30.0))

    def stop_inside_backoff(current: FakeClock) -> None:
        if current.now > NEXT:
            loop.request_stop()

    clock.on_sleep = stop_inside_backoff
    assert loop.run(max_cycles=5) == 1
    assert runner.calls == 1
    assert clock.sleeps == [898.0, 30.0]
    assert loop.history[0].outcome is PollOutcome.RECOVERABLE_FAILURE
    assert loop.history[0].next_poll_at == NEXT + timedelta(seconds=30)  # planned, never run
    assert loop.stop_requested is True


def test_no_new_cycle_starts_after_a_stop_even_when_the_target_is_due() -> None:
    loop, clock, runner = make_loop([report()])
    clock.on_sleep = lambda _: loop.request_stop()  # the stop lands as 12:30:00 arrives
    assert loop.run(max_cycles=3) == 0
    assert clock.now == NEXT  # the target time was reached...
    assert runner.calls == 0  # ...and still no cycle ran
    # A stopped loop stays stopped: a later run polls nothing and waits for nothing.
    assert loop.run(max_cycles=3) == 0
    assert clock.sleeps == [898.0]
    assert runner.calls == 0


def test_the_first_stop_reason_wins() -> None:
    loop, _, _ = make_loop()
    loop.request_stop("first")
    loop.request_stop("second")
    assert loop.stop_reason == "first"


def test_max_cycles_bounds_a_run_without_requesting_a_stop() -> None:
    loop, _, runner = make_loop([report()])
    assert loop.run(max_cycles=3) == 3
    assert loop.stop_requested is False
    assert loop.run(max_cycles=2) == 2  # resumable: the grid continues
    assert loop.cycles == 5 and runner.calls == 5
    assert runner.started_at == [NEXT, AFTER_NEXT, at(13, 0), at(13, 15), at(13, 30)]
    with pytest.raises(LiveConfigurationError):
        loop.run(max_cycles=0)


def test_run_is_not_reentrant() -> None:
    def reenter(_: PollCycleResult) -> None:
        loop.run(max_cycles=1)  # an observer that misbehaves

    loop, _, _ = make_loop([report()], on_cycle=reenter)
    with pytest.raises(RuntimeError, match="already running"):
        loop.run(max_cycles=1)
    assert loop.running is False


def test_a_frozen_clock_fails_loudly_instead_of_spinning_forever() -> None:
    # A sleep boundary that never advances the clock is a harness bug; the loop
    # detects the stall after a bounded number of waits rather than hanging.
    loop, clock, runner = make_loop([report()], sleeper=lambda _: None)
    with pytest.raises(RuntimeError, match="did not advance"):
        loop.run(max_cycles=1)
    assert runner.calls == 0
    assert clock.sleeps == []


# --------------------------------------------------------------------------
# Signal helper
# --------------------------------------------------------------------------


def test_signal_helper_installs_handlers_that_only_request_stop() -> None:
    loop, _, runner = make_loop([report()])
    registered: dict[int, Callable[[int, FrameType | None], None]] = {}

    def fake_register(
        signum: int, handler: Callable[[int, FrameType | None], None]
    ) -> Callable[[int, FrameType | None], None] | int | signal.Handlers | None:
        registered[signum] = handler
        return signal.SIG_DFL

    previous = install_stop_signal_handlers(loop, register=fake_register)
    assert set(registered) == {int(signal.SIGINT), int(signal.SIGTERM)}
    assert previous == {int(signal.SIGINT): signal.SIG_DFL, int(signal.SIGTERM): signal.SIG_DFL}
    handler = registered[int(signal.SIGTERM)]
    assert handler is registered[int(signal.SIGINT)]  # one handler, one behaviour
    assert loop.stop_requested is False
    handler(int(signal.SIGTERM), None)
    assert loop.stop_requested is True
    assert loop.stop_reason == "signal SIGTERM"
    assert runner.calls == 0  # the handler never runs a cycle
    # The handler body touches exactly the stop entry point and the name helper:
    # no persistence, delivery, network, or logging call is even referenced.
    assert set(handler.__code__.co_names) == {"request_stop", "_signal_name"}
    assert handler.__code__.co_freevars == ("loop",)
    # A second signal keeps the first reason; nothing else changes.
    registered[int(signal.SIGINT)](int(signal.SIGINT), None)
    assert loop.stop_reason == "signal SIGTERM"


def test_signal_helper_names_unknown_signal_numbers_without_failing() -> None:
    loop, _, _ = make_loop()
    captured: list[Callable[[int, FrameType | None], None]] = []

    def fake_register(
        signum: int, handler: Callable[[int, FrameType | None], None]
    ) -> Callable[[int, FrameType | None], None] | int | signal.Handlers | None:
        captured.append(handler)
        return None

    install_stop_signal_handlers(loop, signals=(int(signal.SIGTERM),), register=fake_register)
    captured[0](999_999, None)
    assert loop.stop_reason == "signal 999999"


def test_signal_helper_requires_a_poll_loop() -> None:
    with pytest.raises(LiveConfigurationError):
        install_stop_signal_handlers(object(), register=lambda s, h: None)  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery semantics")
def test_a_real_sigterm_requests_stop_and_the_loop_exits_between_chunks() -> None:
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("signal handlers can only be installed from the main thread")
    loop, clock, runner = make_loop([report()], settings=config(chunk=60.0))
    previous = install_stop_signal_handlers(loop, signals=(signal.SIGTERM,))
    try:
        installed = signal.getsignal(signal.SIGTERM)
        assert callable(installed) and installed is not previous[int(signal.SIGTERM)]

        def raise_on_third_chunk(current: FakeClock) -> None:
            if len(current.sleeps) == 3:
                signal.raise_signal(signal.SIGTERM)

        clock.on_sleep = raise_on_third_chunk
        executed = loop.run(max_cycles=1)
    finally:
        signal.signal(signal.SIGTERM, previous[int(signal.SIGTERM)])
    assert executed == 0
    assert runner.calls == 0
    assert loop.stop_requested is True
    assert loop.stop_reason == "signal SIGTERM"
    assert clock.sleeps == [60.0] * 3  # the flag was seen before the fourth chunk
    assert signal.getsignal(signal.SIGTERM) is previous[int(signal.SIGTERM)]


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_default_configuration_values() -> None:
    settings = LivePollLoopConfig("15m")
    assert settings.jitter_min_seconds == DEFAULT_POLL_JITTER_MIN_SECONDS == 1.0
    assert settings.jitter_max_seconds == DEFAULT_POLL_JITTER_MAX_SECONDS == 5.0
    assert settings.backoff_base_seconds == 1.0
    assert settings.backoff_max_seconds == 60.0
    assert settings.max_consecutive_failures is None
    assert settings.wait_chunk_seconds == DEFAULT_POLL_WAIT_CHUNK_SECONDS == 1.0
    assert settings.interval_seconds == 900
    assert dataclasses.is_dataclass(settings)
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.timeframe = "1h"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"timeframe": ""}, "nonempty"),
        ({"timeframe": "7m"}, "invalid"),
        ({"timeframe": "1M"}, "invalid"),
        ({"jitter_min_seconds": -0.1}, "jitter_min_seconds must not be negative"),
        ({"jitter_max_seconds": float("nan")}, "jitter_max_seconds must be a finite"),
        ({"jitter_min_seconds": 3.0, "jitter_max_seconds": 2.0}, "must not be below"),
        ({"jitter_max_seconds": 900.0}, "smaller than the 15m interval"),
        ({"jitter_max_seconds": True}, "finite number"),
        ({"backoff_base_seconds": 0.0}, "greater than zero"),
        ({"backoff_base_seconds": float("inf")}, "finite number"),
        ({"backoff_max_seconds": 0.5}, "backoff_max_seconds must not be below"),
        ({"max_consecutive_failures": 0}, "positive integer or None"),
        ({"max_consecutive_failures": True}, "positive integer or None"),
        ({"max_consecutive_failures": 2.0}, "positive integer or None"),
        ({"wait_chunk_seconds": 0.0}, "greater than zero"),
        ({"wait_chunk_seconds": "1"}, "finite number"),
    ],
)
def test_configuration_rejects_invalid_values(overrides: dict[str, object], message: str) -> None:
    fields: dict[str, object] = {"timeframe": "15m", **overrides}
    with pytest.raises(LiveConfigurationError, match=message):
        LivePollLoopConfig(**fields)  # type: ignore[arg-type]


def test_load_poll_loop_config_from_a_mapping() -> None:
    settings = load_poll_loop_config(
        {
            "LIVE_TIMEFRAME": " 5m ",
            "LIVE_POLL_JITTER_MIN_SECONDS": "0.5",
            "LIVE_POLL_JITTER_MAX_SECONDS": "2",
            "LIVE_POLL_BACKOFF_BASE_SECONDS": "2.0",
            "LIVE_POLL_BACKOFF_MAX_SECONDS": "120",
            "LIVE_POLL_MAX_CONSECUTIVE_FAILURES": "12",
            "LIVE_POLL_WAIT_CHUNK_SECONDS": "0.25",
        }
    )
    assert settings == LivePollLoopConfig(
        timeframe="5m",
        jitter_min_seconds=0.5,
        jitter_max_seconds=2.0,
        backoff_base_seconds=2.0,
        backoff_max_seconds=120.0,
        max_consecutive_failures=12,
        wait_chunk_seconds=0.25,
    )


def test_load_poll_loop_config_defaults_and_blank_values() -> None:
    settings = load_poll_loop_config(
        {
            "LIVE_TIMEFRAME": "15m",
            "LIVE_POLL_JITTER_MAX_SECONDS": "  ",
            "LIVE_POLL_MAX_CONSECUTIVE_FAILURES": "",
        }
    )
    assert settings == LivePollLoopConfig("15m")


def test_load_poll_loop_config_reads_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in list(os.environ):
        if name.startswith("LIVE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LIVE_TIMEFRAME", "1h")
    monkeypatch.setenv("LIVE_POLL_BACKOFF_MAX_SECONDS", "300")
    settings = load_poll_loop_config()
    assert settings.timeframe == "1h"
    assert settings.backoff_max_seconds == 300.0
    monkeypatch.delenv("LIVE_TIMEFRAME")
    with pytest.raises(LiveConfigurationError, match="LIVE_TIMEFRAME is required"):
        load_poll_loop_config()


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "LIVE_TIMEFRAME is required"),
        ({"LIVE_TIMEFRAME": "15m", "LIVE_POLL_JITTER_MAX_SECONDS": "fast"}, "number of seconds"),
        ({"LIVE_TIMEFRAME": "15m", "LIVE_POLL_BACKOFF_BASE_SECONDS": "-1"}, "greater than zero"),
        ({"LIVE_TIMEFRAME": "15m", "LIVE_POLL_MAX_CONSECUTIVE_FAILURES": "many"}, "integer"),
        ({"LIVE_TIMEFRAME": "15m", "LIVE_POLL_MAX_CONSECUTIVE_FAILURES": "1.5"}, "integer"),
        ({"LIVE_TIMEFRAME": "2m"}, "invalid"),
    ],
)
def test_load_poll_loop_config_rejects_malformed_values(env: dict[str, str], message: str) -> None:
    with pytest.raises(LiveConfigurationError, match=message):
        load_poll_loop_config(env)


def test_poll_loop_and_live_service_share_one_timeframe_variable() -> None:
    env = {"LIVE_SYMBOL": "BTCUSDT", "LIVE_TIMEFRAME": "15m", "LIVE_ENABLED": "true"}
    assert load_poll_loop_config(env).timeframe == load_live_config(env).timeframe == "15m"


def test_loop_constructor_validates_its_collaborators() -> None:
    with pytest.raises(LiveConfigurationError, match="CycleRunner"):
        LivePollLoop(object(), config())  # type: ignore[arg-type]
    with pytest.raises(LiveConfigurationError, match="LivePollLoopConfig"):
        LivePollLoop(ScriptedRunner([report()]), "15m")  # type: ignore[arg-type]
    with pytest.raises(LiveConfigurationError, match="history_limit"):
        LivePollLoop(ScriptedRunner([report()]), config(), history_limit=0)


# --------------------------------------------------------------------------
# Results, history, and observers
# --------------------------------------------------------------------------


def test_history_is_bounded_while_the_lifetime_counter_is_not() -> None:
    loop, _, _ = make_loop([report()], history_limit=3)
    loop.run(max_cycles=5)
    assert loop.cycles == 5
    assert [r.sequence for r in loop.history] == [3, 4, 5]
    assert loop.last_result is loop.history[-1]


def test_observer_receives_every_result_in_order_before_the_next_wait() -> None:
    seen: list[tuple[int, int]] = []
    loop, clock, _ = make_loop(
        [report()], on_cycle=lambda r: seen.append((r.sequence, len(clock.sleeps)))
    )
    loop.run(max_cycles=3)
    assert seen == [(1, 1), (2, 2), (3, 3)]


def test_observer_exceptions_surface_and_stop_the_loop() -> None:
    def broken(_: PollCycleResult) -> None:
        raise ValueError("observer failed")

    loop, clock, runner = make_loop([report()], on_cycle=broken)
    with pytest.raises(ValueError, match="observer failed"):
        loop.run(max_cycles=3)
    assert runner.calls == 1
    assert clock.sleeps == [898.0]
    assert loop.running is False


def test_results_are_immutable_plain_data() -> None:
    loop, _, _ = make_loop([LiveFeedError("down"), report()])
    loop.run(max_cycles=2)
    failed, succeeded = loop.history
    assert isinstance(failed.error, str)  # rendered text, never the exception object
    assert failed.report is None
    assert isinstance(succeeded.report, CycleReport)
    assert succeeded.error is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        failed.outcome = PollOutcome.SUCCESS  # type: ignore[misc]
    assert {outcome.value for outcome in PollOutcome} == {
        "success",
        "recoverable_failure",
        "fatal_failure",
    }
    assert loop.history is not loop.history  # a fresh read-only copy each time
    assert loop.history == loop.history


# --------------------------------------------------------------------------
# Architecture: run_cycle only, public seams only, no transport, no network
# --------------------------------------------------------------------------


class StrictRunner:
    """Fails on any attribute access other than ``run_cycle``."""

    def __init__(self) -> None:
        self.calls = 0

    def run_cycle(self) -> CycleReport:
        self.calls += 1
        return report()

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the poll loop touched runner.{name}")


def test_loop_calls_only_run_cycle_on_its_runner() -> None:
    clock = FakeClock()
    runner = StrictRunner()
    loop = LivePollLoop(runner, config(), clock=clock, sleep_fn=clock.sleep)
    assert loop.run(max_cycles=2) == 2
    assert runner.calls == 2


def test_live_service_satisfies_the_cycle_runner_protocol_and_runs_under_the_loop() -> None:
    # The real Phase 33 service over the scripted offline transport: the loop
    # drives it on the 15m grid and receives its unchanged CycleReports.
    service = scripted_service((6, 9, 13, 17))
    assert isinstance(service, CycleRunner)
    clock = FakeClock()
    loop = LivePollLoop(service, config(), clock=clock, sleep_fn=clock.sleep)
    assert loop.run(max_cycles=2) == 2
    first, second = loop.history
    assert first.outcome is second.outcome is PollOutcome.SUCCESS
    assert first.report is not None and first.report.frames == 3
    assert second.report is not None and second.report.frames == 4
    assert first.report.buy_signals == second.report.buy_signals == 1
    assert service.cycles == 2
    assert clock.sleeps == [898.0, 900.0]


def test_loop_never_sleeps_for_real_or_starts_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_sleep(seconds: float) -> None:
        raise AssertionError("the loop must use the injected sleep boundary")

    def forbidden_thread(self: threading.Thread) -> None:
        raise AssertionError("the loop must not start threads")

    monkeypatch.setattr(time, "sleep", forbidden_sleep)
    monkeypatch.setattr(threading.Thread, "start", forbidden_thread)
    loop, clock, _ = make_loop([LiveFeedError("down"), report()])
    assert loop.run(max_cycles=3) == 3
    assert clock.sleeps == [898.0, 1.0, 899.0]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


POLL_LOOP_SOURCE = Path(poll_loop_module.__file__)


def test_poll_loop_imports_only_stdlib_and_sanctioned_public_seams() -> None:
    imports = _imports(POLL_LOOP_SOURCE)
    project = {name for name in imports if name.split(".")[0] == "smcsignal"}
    assert project == {
        "smcsignal.analysis.errors",
        "smcsignal.analysis.mtf.timeframes",
        "smcsignal.data.errors",
        "smcsignal.live.config",
        "smcsignal.live.market_feed",
        "smcsignal.live.retry_after",
        "smcsignal.live.service",
    }
    for name in imports - project:
        assert name.split(".")[0] in sys.stdlib_module_names, name
    banned = {
        "threading",
        "asyncio",
        "socket",
        "subprocess",
        "sched",
        "concurrent",
        "http",
        "urllib",
    }
    assert not {name.split(".")[0] for name in imports} & banned


def test_poll_loop_names_no_private_or_analysis_internal_api() -> None:
    tree = ast.parse(POLL_LOOP_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module is not None
            assert not node.module.startswith("smcsignal.analysis.") or node.module in {
                "smcsignal.analysis.errors",
                "smcsignal.analysis.mtf.timeframes",
            }
            for alias in node.names:
                assert not alias.name.startswith("_"), alias.name
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
        ):
            # Private attribute access is confined to the loop's own state.
            assert isinstance(node.value, ast.Name) and node.value.id == "self", ast.dump(node)
        if isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Attribute):
                assert callee.attr not in {
                    "deliver",
                    "persist",
                    "update",
                    "poll",
                    "warm_up",
                    "save",
                    "load",
                }
    text = POLL_LOOP_SOURCE.read_text(encoding="utf-8")
    assert "run_cycle" in text
    for forbidden in (
        "Analyzer",
        "HistoricalReplay",
        "TelegramSink",
        "HttpTransport",
        "DatasetStore",
    ):
        assert forbidden not in text, forbidden


def test_package_exports_the_poll_loop_api() -> None:
    for name in (
        "PollOutcome",
        "PollCycleResult",
        "LivePollLoopConfig",
        "load_poll_loop_config",
        "next_poll_time",
        "CycleRunner",
        "LivePollLoop",
        "install_stop_signal_handlers",
        "classify_failure",
    ):
        assert name in smcsignal.live.__all__
        assert getattr(smcsignal.live, name) is getattr(poll_loop_module, name)
