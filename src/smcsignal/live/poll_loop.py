"""Phase 35A operator-grade poll loop: candle-boundary scheduling over ``run_cycle``.

The loop is the smallest operator-grade driver for the Phase 33 live service.
It decides *when* to call the service and nothing else:

    clock
      → candle-boundary scheduler (UTC grid, strictly after "now")
        → bounded additive jitter
          → CycleRunner.run_cycle()            (``LiveService.run_cycle``, unchanged)
              success     → next absolute candle boundary
              recoverable → capped exponential backoff, then retry
              fatal       → stop and surface the exception
    stop request → finish the current synchronous operation → exit cleanly

Approved Phase 35A decisions
----------------------------

- Standard library only; an injected clock, sleep function, and random
  source; no threads, no scheduler dependency, no persistent scheduler state.
- Scheduling is grid-based and drift-free. Every normal poll targets the first
  candle boundary *strictly after* the current time on the timeframe's UTC
  grid, so a poll that lands exactly on a boundary schedules the *next*
  boundary instead of re-polling at once: on a 15m grid, 12:15:00 → 12:30:00.
  The grid is anchored at the Unix epoch (the ``1w`` grid at the first Monday,
  1970-01-05, which is where Binance opens its weekly candle).
- Jitter is additive and bounded to ``[jitter_min_seconds, jitter_max_seconds]``
  with ``jitter_max_seconds`` strictly smaller than the candle interval: a poll
  never runs before its boundary and never reaches the following one.
- Recoverable failures — ``LiveFeedError``, ``DataProviderError``, and
  ``DataValidationError`` — retry after ``min(base * 2**(n-1), cap)`` seconds
  for the n-th consecutive failure; the counter resets on the next success.
  ``DataValidationError`` is recoverable *here* because a malformed or partial
  upstream page is an operational condition of the moment, not an immutable
  configuration or state failure; the feed's own never-retry rule for it is
  untouched. An optional consecutive-failure budget turns a persistent outage
  into a fatal stop so a supervisor can restart the process.
- Everything else — configuration, state, and contract failures, and any
  unclassified exception — is fatal: the loop records the result, stops, and
  re-raises, so the process exits nonzero and an external supervisor restarts
  it through the deterministic Phase 33 restart path (persisted candle window
  plus verified ledger recovery). The loop never restarts the service itself.
- Waits are chunked and interruptible; ``request_stop()`` only assigns a flag
  and is honored between chunks and between cycles, never inside a cycle, so a
  cycle's ledger persistence and delivery always complete.

The loop calls exactly one thing on its runner: the public ``run_cycle()``. It
imports no analyzer, holds no ledger, no dataset store, and no Telegram
transport, and it performs no trading, order, position, sizing, broker, or
execution action of any kind. Importing this module performs no IO.
"""

from __future__ import annotations

import math
import os
import random
import signal
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import FrameType
from typing import Protocol, runtime_checkable

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.mtf.timeframes import timeframe_seconds
from smcsignal.data.errors import DataProviderError, DataValidationError
from smcsignal.live.config import LiveConfigurationError
from smcsignal.live.market_feed import LiveFeedError
from smcsignal.live.service import CycleReport

DEFAULT_POLL_JITTER_MIN_SECONDS = 1.0
DEFAULT_POLL_JITTER_MAX_SECONDS = 5.0
DEFAULT_POLL_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_POLL_BACKOFF_MAX_SECONDS = 60.0
DEFAULT_POLL_WAIT_CHUNK_SECONDS = 1.0
DEFAULT_POLL_HISTORY_LIMIT = 256

RECOVERABLE_ERRORS: tuple[type[Exception], ...] = (
    LiveFeedError,
    DataProviderError,
    DataValidationError,
)

_TIMEFRAME = "LIVE_TIMEFRAME"
_JITTER_MIN = "LIVE_POLL_JITTER_MIN_SECONDS"
_JITTER_MAX = "LIVE_POLL_JITTER_MAX_SECONDS"
_BACKOFF_BASE = "LIVE_POLL_BACKOFF_BASE_SECONDS"
_BACKOFF_MAX = "LIVE_POLL_BACKOFF_MAX_SECONDS"
_MAX_FAILURES = "LIVE_POLL_MAX_CONSECUTIVE_FAILURES"
_WAIT_CHUNK = "LIVE_POLL_WAIT_CHUNK_SECONDS"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FIRST_MONDAY = datetime(1970, 1, 5, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_MAX_BACKOFF_EXPONENT = 64
_MAX_STALLED_WAITS = 10_000

Clock = Callable[[], datetime]
SleepFn = Callable[[float], None]
RandomSource = Callable[[], float]
StopSignalHandler = Callable[[int, FrameType | None], None]
SignalHandler = Callable[[int, FrameType | None], object] | int | signal.Handlers | None
SignalRegistrar = Callable[[int, StopSignalHandler], SignalHandler]


# -- outcomes and results -----------------------------------------------------------


class PollOutcome(Enum):
    """How one poll cycle ended, as the loop classified it."""

    SUCCESS = "success"
    RECOVERABLE_FAILURE = "recoverable_failure"
    FATAL_FAILURE = "fatal_failure"


@dataclass(frozen=True, slots=True)
class PollCycleResult:
    """One executed cycle's plain, immutable record.

    ``error`` is the rendered ``TypeName: message`` of the failure (never the
    exception object, so results hold no traceback); ``report`` is the runner's
    own ``CycleReport`` on success; ``next_poll_at`` is the follow-up the loop
    scheduled, or ``None`` when this run would not poll again after the cycle
    (fatal failure, stop request, or the ``max_cycles`` bound).
    """

    sequence: int
    outcome: PollOutcome
    scheduled_at: datetime
    started_at: datetime
    finished_at: datetime
    report: CycleReport | None
    error: str | None
    consecutive_failures: int
    next_poll_at: datetime | None

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


@runtime_checkable
class CycleRunner(Protocol):
    """Anything exposing the Phase 33 public cycle: ``LiveService`` satisfies it."""

    def run_cycle(self) -> CycleReport: ...


# -- configuration --------------------------------------------------------------------


def _require_seconds(value: object, name: str, *, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LiveConfigurationError(f"{name} must be a finite number of seconds")
    if positive and value <= 0:
        raise LiveConfigurationError(f"{name} must be greater than zero")
    if not positive and value < 0:
        raise LiveConfigurationError(f"{name} must not be negative")
    return float(value)


def _interval_seconds(timeframe: object) -> int:
    if not isinstance(timeframe, str) or not timeframe.strip():
        raise LiveConfigurationError("timeframe must be a nonempty string")
    try:
        return timeframe_seconds(timeframe)
    except AnalysisConfigurationError as exc:
        raise LiveConfigurationError(
            f"poll loop timeframe {timeframe!r} is invalid: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class LivePollLoopConfig:
    """Validated scheduling policy for one poll loop; constructing it starts nothing.

    ``timeframe`` is the primary candle grid (the same value the live service
    polls, normally loaded from the same ``LIVE_TIMEFRAME`` variable);
    ``jitter_min_seconds``/``jitter_max_seconds`` bound the additive delay after
    each boundary; ``backoff_base_seconds``/``backoff_max_seconds`` define the
    capped exponential retry delay; ``max_consecutive_failures`` (``None`` =
    unbounded) turns a persistent recoverable outage into a fatal stop;
    ``wait_chunk_seconds`` bounds how long a stop request can go unnoticed.
    """

    timeframe: str
    jitter_min_seconds: float = DEFAULT_POLL_JITTER_MIN_SECONDS
    jitter_max_seconds: float = DEFAULT_POLL_JITTER_MAX_SECONDS
    backoff_base_seconds: float = DEFAULT_POLL_BACKOFF_BASE_SECONDS
    backoff_max_seconds: float = DEFAULT_POLL_BACKOFF_MAX_SECONDS
    max_consecutive_failures: int | None = None
    wait_chunk_seconds: float = DEFAULT_POLL_WAIT_CHUNK_SECONDS

    def __post_init__(self) -> None:
        interval = _interval_seconds(self.timeframe)
        low = _require_seconds(self.jitter_min_seconds, "jitter_min_seconds", positive=False)
        high = _require_seconds(self.jitter_max_seconds, "jitter_max_seconds", positive=False)
        if high < low:
            raise LiveConfigurationError("jitter_max_seconds must not be below jitter_min_seconds")
        if high >= interval:
            raise LiveConfigurationError(
                f"jitter_max_seconds must be smaller than the {self.timeframe} interval "
                f"({interval} seconds)"
            )
        base = _require_seconds(self.backoff_base_seconds, "backoff_base_seconds", positive=True)
        cap = _require_seconds(self.backoff_max_seconds, "backoff_max_seconds", positive=True)
        if cap < base:
            raise LiveConfigurationError(
                "backoff_max_seconds must not be below backoff_base_seconds"
            )
        budget = self.max_consecutive_failures
        if budget is not None and (type(budget) is not int or budget < 1):
            raise LiveConfigurationError(
                "max_consecutive_failures must be a positive integer or None"
            )
        _require_seconds(self.wait_chunk_seconds, "wait_chunk_seconds", positive=True)

    @property
    def interval_seconds(self) -> int:
        """The exact UTC duration of one primary candle."""

        return timeframe_seconds(self.timeframe)

    def backoff_seconds(self, consecutive_failures: int) -> float:
        """Capped exponential delay before the retry that follows the n-th failure."""

        if type(consecutive_failures) is not int or consecutive_failures < 1:
            raise LiveConfigurationError("consecutive_failures must be a positive integer")
        exponent = min(consecutive_failures - 1, _MAX_BACKOFF_EXPONENT)
        delay = float(self.backoff_base_seconds) * (2.0**exponent)
        return min(delay, float(self.backoff_max_seconds))


def _required(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if value is None or not value.strip():
        raise LiveConfigurationError(f"{name} is required for the live poll loop")
    return value.strip()


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _float_setting(source: Mapping[str, str], name: str, default: float) -> float:
    raw = _optional(source, name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LiveConfigurationError(f"{name} must be a number of seconds, got {raw!r}") from exc


def load_poll_loop_config(source: Mapping[str, str] | None = None) -> LivePollLoopConfig:
    """Load and validate the poll-loop environment mapping (``os.environ`` by default).

    The grid comes from the same ``LIVE_TIMEFRAME`` variable the live service
    reads, so the loop and the feed cannot disagree about the candle interval.
    Every optional value falls back to the documented default; malformed values
    are rejected before anything is constructed. Loading starts nothing.
    """

    env = os.environ if source is None else source
    timeframe = _required(env, _TIMEFRAME)
    raw_budget = _optional(env, _MAX_FAILURES)
    if raw_budget is None:
        budget: int | None = None
    else:
        try:
            budget = int(raw_budget)
        except ValueError as exc:
            raise LiveConfigurationError(
                f"{_MAX_FAILURES} must be an integer, got {raw_budget!r}"
            ) from exc
    return LivePollLoopConfig(
        timeframe=timeframe,
        jitter_min_seconds=_float_setting(env, _JITTER_MIN, DEFAULT_POLL_JITTER_MIN_SECONDS),
        jitter_max_seconds=_float_setting(env, _JITTER_MAX, DEFAULT_POLL_JITTER_MAX_SECONDS),
        backoff_base_seconds=_float_setting(env, _BACKOFF_BASE, DEFAULT_POLL_BACKOFF_BASE_SECONDS),
        backoff_max_seconds=_float_setting(env, _BACKOFF_MAX, DEFAULT_POLL_BACKOFF_MAX_SECONDS),
        max_consecutive_failures=budget,
        wait_chunk_seconds=_float_setting(env, _WAIT_CHUNK, DEFAULT_POLL_WAIT_CHUNK_SECONDS),
    )


# -- the candle grid ------------------------------------------------------------------


def next_poll_time(now: datetime, timeframe: str) -> datetime:
    """Return the first candle boundary *strictly after* ``now`` on the UTC grid.

    Strictly after is the anti-spin rule: a time exactly on a boundary yields
    the following boundary (15m: 12:15:00 → 12:30:00, never 12:15:00 itself).
    The computation is absolute integer arithmetic over the epoch-anchored grid
    (Monday-anchored for ``1w``), so repeated scheduling accumulates no drift.
    ``now`` must be timezone-aware; the result is always expressed in UTC.
    """

    interval_us = _interval_seconds(timeframe) * 1_000_000
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise LiveConfigurationError("now must be a timezone-aware datetime")
    anchor = _FIRST_MONDAY if timeframe == "1w" else _EPOCH
    elapsed_us = (now - anchor) // _MICROSECOND
    steps = elapsed_us // interval_us + 1
    return anchor + timedelta(microseconds=steps * interval_us)


def classify_failure(error: BaseException) -> PollOutcome:
    """Recoverable for the transient feed/provider family; fatal for everything else."""

    if isinstance(error, RECOVERABLE_ERRORS):
        return PollOutcome.RECOVERABLE_FAILURE
    return PollOutcome.FATAL_FAILURE


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# -- the loop ----------------------------------------------------------------------------


class LivePollLoop:
    """Drive a ``CycleRunner`` on the candle grid until stopped or fatally failed.

    The loop owns only scheduling state: the stop flag, the consecutive-failure
    counter, the next target time, and a bounded in-memory history of results.
    It calls ``runner.run_cycle()`` and nothing else on the runner.
    """

    def __init__(
        self,
        runner: CycleRunner,
        config: LivePollLoopConfig,
        *,
        clock: Clock | None = None,
        sleep_fn: SleepFn | None = None,
        random_source: RandomSource | None = None,
        on_cycle: Callable[[PollCycleResult], None] | None = None,
        history_limit: int = DEFAULT_POLL_HISTORY_LIMIT,
    ) -> None:
        if not isinstance(runner, CycleRunner) or not callable(runner.run_cycle):
            raise LiveConfigurationError("poll loop requires a CycleRunner exposing run_cycle()")
        if not isinstance(config, LivePollLoopConfig):
            raise LiveConfigurationError("poll loop requires a LivePollLoopConfig")
        if type(history_limit) is not int or history_limit < 1:
            raise LiveConfigurationError("history_limit must be a positive integer")
        self._runner = runner
        self._config = config
        self._clock: Clock = clock if clock is not None else _utc_now
        self._sleep: SleepFn = sleep_fn if sleep_fn is not None else time.sleep
        self._random: RandomSource = random_source if random_source is not None else random.random
        self._on_cycle = on_cycle
        self._history: deque[PollCycleResult] = deque(maxlen=history_limit)
        self._stop_requested = False
        self._stop_reason: str | None = None
        self._running = False
        self._cycles = 0
        self._consecutive_failures = 0
        self._next_poll_at: datetime | None = None

    # -- state exposed for operators and tests ------------------------------------

    @property
    def runner(self) -> CycleRunner:
        return self._runner

    @property
    def config(self) -> LivePollLoopConfig:
        return self._config

    @property
    def cycles(self) -> int:
        """Cycles executed over the loop's lifetime (all ``run`` calls)."""

        return self._cycles

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def running(self) -> bool:
        return self._running

    @property
    def next_poll_at(self) -> datetime | None:
        """The currently scheduled target, or ``None`` when nothing is scheduled."""

        return self._next_poll_at

    @property
    def history(self) -> tuple[PollCycleResult, ...]:
        """The most recent results, oldest first, bounded by ``history_limit``."""

        return tuple(self._history)

    @property
    def last_result(self) -> PollCycleResult | None:
        return self._history[-1] if self._history else None

    # -- shutdown ------------------------------------------------------------------------

    def request_stop(self, reason: str = "stop requested") -> None:
        """Ask the loop to exit after its current synchronous operation.

        Safe to call from a signal handler or another thread: it assigns the
        stop flag and a reason and does nothing else — no IO, no persistence,
        no delivery, no network. The first reason wins so the earliest cause
        is the one reported. A running cycle always finishes; the loop then
        exits instead of scheduling or waiting for the next one.
        """

        if not self._stop_requested:
            self._stop_requested = True
            self._stop_reason = reason

    # -- the loop --------------------------------------------------------------------------

    def run(self, *, max_cycles: int | None = None) -> int:
        """Poll on the grid until stopped, fatally failed, or ``max_cycles`` cycles ran.

        Returns the number of cycles this call executed. A recoverable failure
        is retried after the capped exponential backoff; a fatal failure is
        recorded in the history, handed to ``on_cycle``, and then re-raised
        unchanged so the caller (and any supervisor) sees it.
        """

        if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
            raise LiveConfigurationError("max_cycles must be a positive integer or None")
        if self._running:
            raise RuntimeError("the poll loop is already running")
        self._running = True
        executed = 0
        try:
            if self._stop_requested:
                return 0
            target = self._schedule_boundary()
            while self._wait_until(target):
                started = self._clock()
                report: CycleReport | None = None
                failure: Exception | None = None
                try:
                    report = self._runner.run_cycle()
                except Exception as exc:
                    failure = exc
                finished = self._clock()
                executed += 1
                self._cycles += 1

                error: str | None = None
                if failure is None:
                    outcome = PollOutcome.SUCCESS
                    self._consecutive_failures = 0
                else:
                    outcome = classify_failure(failure)
                    error = _describe(failure)
                    if outcome is PollOutcome.RECOVERABLE_FAILURE:
                        self._consecutive_failures += 1
                        budget = self._config.max_consecutive_failures
                        if budget is not None and self._consecutive_failures >= budget:
                            outcome = PollOutcome.FATAL_FAILURE
                            error = (
                                "retry budget exhausted after "
                                f"{self._consecutive_failures} consecutive recoverable "
                                f"failures: {error}"
                            )

                stopping = (
                    outcome is PollOutcome.FATAL_FAILURE
                    or self._stop_requested
                    or (max_cycles is not None and executed >= max_cycles)
                )
                if stopping:
                    next_at: datetime | None = None
                    self._next_poll_at = None
                elif outcome is PollOutcome.SUCCESS:
                    next_at = self._schedule_boundary()
                else:
                    next_at = self._schedule_backoff()

                result = PollCycleResult(
                    sequence=self._cycles,
                    outcome=outcome,
                    scheduled_at=target,
                    started_at=started,
                    finished_at=finished,
                    report=report,
                    error=error,
                    consecutive_failures=self._consecutive_failures,
                    next_poll_at=next_at,
                )
                self._history.append(result)
                if self._on_cycle is not None:
                    self._on_cycle(result)
                if outcome is PollOutcome.FATAL_FAILURE:
                    assert failure is not None
                    raise failure
                if next_at is None:
                    break
                target = next_at
            return executed
        finally:
            self._running = False

    # -- scheduling mechanics -------------------------------------------------------------

    def _jitter(self) -> float:
        low = float(self._config.jitter_min_seconds)
        high = float(self._config.jitter_max_seconds)
        if high <= low:
            return low  # a zero-width band never consults the random source
        fraction = self._random()
        if (
            isinstance(fraction, bool)
            or not isinstance(fraction, (int, float))
            or not math.isfinite(fraction)
        ):
            raise LiveConfigurationError("random_source must return a finite number")
        bounded = min(max(float(fraction), 0.0), 1.0)
        return low + (high - low) * bounded

    def _schedule_boundary(self) -> datetime:
        boundary = next_poll_time(self._clock(), self._config.timeframe)
        target = boundary + timedelta(seconds=self._jitter())
        self._next_poll_at = target
        return target

    def _schedule_backoff(self) -> datetime:
        delay = self._config.backoff_seconds(self._consecutive_failures)
        target = self._clock() + timedelta(seconds=delay)
        self._next_poll_at = target
        return target

    def _wait_until(self, target: datetime) -> bool:
        """Sleep in interruptible chunks; True when due, False when stop was requested."""

        stalled = 0
        previous: datetime | None = None
        while True:
            if self._stop_requested:
                return False
            now = self._clock()
            remaining = (target - now).total_seconds()
            if remaining <= 0:
                return True
            if previous is not None and now <= previous:
                stalled += 1
                if stalled >= _MAX_STALLED_WAITS:
                    raise RuntimeError(
                        f"the clock did not advance across {stalled} consecutive waits; "
                        "the injected clock or sleep boundary is broken"
                    )
            else:
                stalled = 0
            previous = now
            self._sleep(min(remaining, float(self._config.wait_chunk_seconds)))


# -- optional signal helper ---------------------------------------------------------------


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


def install_stop_signal_handlers(
    loop: LivePollLoop,
    *,
    signals: Sequence[int] = (signal.SIGINT, signal.SIGTERM),
    register: SignalRegistrar = signal.signal,
) -> dict[int, SignalHandler]:
    """Route SIGINT/SIGTERM to ``loop.request_stop()``; return the previous handlers.

    The installed handler assigns the stop flag and nothing else — no
    persistence, no delivery, no network, no logging — so a signal never
    interrupts the running cycle; it only prevents the next one, and the loop
    notices within one wait chunk. ``register`` defaults to ``signal.signal``
    (main thread only, as the standard library requires) and is injectable so
    tests can capture the handler without touching process signal state. The
    caller may restore the returned previous handlers when the loop exits.
    """

    if not isinstance(loop, LivePollLoop):
        raise LiveConfigurationError("install_stop_signal_handlers requires a LivePollLoop")

    def handler(signum: int, frame: FrameType | None) -> None:
        loop.request_stop(f"signal {_signal_name(signum)}")

    previous: dict[int, SignalHandler] = {}
    for signum in signals:
        previous[int(signum)] = register(signum, handler)
    return previous
