"""Phase 35A: the operator-grade poll loop over the existing live service.

This module owns *when* the Phase 33 service runs, never *what* it computes.
Its sole cycle boundary is the public ``run_cycle()`` capability — expressed
as the structural :class:`CycleRunner` protocol, which the real
``LiveService`` satisfies — and it imports no analyzer, no replay engine, no
signal model, and no Telegram transport. The frozen Phase 24 delivery layer
and the frozen Phase 3-17 analysis chain are untouched by this module.

Scheduling model
----------------

Polling aligns to *absolute* primary-timeframe candle boundaries: with a 15m
service the intended wake times are 00:00, 00:15, 00:30, ... regardless of
when the process started or how long a cycle took. :func:`next_poll_time`
computes the next boundary with exact ``timedelta`` arithmetic (no floating
point drift), and bounded additive jitter (an injected random source scaled
by ``jitter_seconds``) can delay a wake but never moves one before the
boundary. A process starting at 12:07:31 wakes at 12:15, never treating the
in-progress candle as available; the provider's closed-candle cutoff remains
the final data-level protection, and the loop only adds scheduling
correctness on top of it.

Failure model
-------------

- A *recoverable* operational error — the bounded market-data retry budget
  being exhausted (``LiveFeedError``), a provider/transport failure
  (``DataProviderError`` family), or a deterministically malformed response
  (``DataValidationError``, which the feed deliberately does not retry inside
  one poll) — applies exponential backoff
  (``base * 2**(n-1)``, capped at ``backoff_max_seconds``), then retries the
  cycle. A success resets the failure count and scheduling returns to the
  next absolute candle boundary, so backoff never permanently destroys
  alignment. Missed candles are never fabricated: the next cycle simply lets
  the existing feed/history mechanism recover whatever closed candles are
  available, and every late wake is recorded with its lateness.
- A *fatal* condition — invalid configuration, corrupt or incompatible
  persisted state, a fail-closed recovery refusal (``AnalysisInputError``),
  or any unexpected error — stops the loop immediately and surfaces
  :class:`LivePollLoopError` (chaining the original) so an external
  supervisor can restart the process. The loop deliberately has no internal
  self-healing restart architecture.

Shutdown and restart
--------------------

``request_stop()`` (optionally wired to SIGINT/SIGTERM by
:func:`request_stop_on_signals`) is observed between bounded sleep chunks, so
shutdown is prompt rather than waiting out a whole candle; the current
synchronous cycle always finishes and its persistence stays atomic. The loop
holds no persistent state of its own — durable progress remains owned by the
existing stores — and a restarted process resumes at the next candle
boundary through the ordinary Phase 33 startup path.

Observability
-------------

Following the repository's records-over-logging convention, the loop emits no
log output: every operational event (scheduled wake, cycle completion,
signals produced, delivery summary, retryable failure, applied backoff, late
wake, fatal stop) is captured in the returned :class:`PollCycleResult`
sequence, available live via ``records`` and on the terminal error. Secrets
never enter any record: the loop never sees the token or chat id at all.

The loop contains no thread, no global state, no import-time behavior, and no
trading, order, position, sizing, or execution capability of any kind.
"""

from __future__ import annotations

import math
import os
import random
import signal
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.mtf.timeframes import timeframe_seconds
from smcsignal.data.errors import (
    DataConfigurationError,
    DataProviderError,
    DataValidationError,
)
from smcsignal.live.config import LiveConfigurationError, LiveServiceConfig
from smcsignal.live.market_feed import LiveFeedError
from smcsignal.live.service import CycleReport

DEFAULT_POLL_JITTER_SECONDS = 0.0
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_MAX_SECONDS = 30.0
DEFAULT_SLEEP_CHUNK_SECONDS = 1.0

_POLL_JITTER = "LIVE_POLL_JITTER_SECONDS"
_POLL_BACKOFF_BASE = "LIVE_POLL_BACKOFF_BASE_SECONDS"
_POLL_BACKOFF_MAX = "LIVE_POLL_BACKOFF_MAX_SECONDS"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class LivePollLoopError(RuntimeError):
    """A fatal poll-loop condition; the loop has stopped.

    ``records`` carries every completed cycle record up to the failure so the
    operator sees exactly what happened before the loop terminated. The
    original cause, when one exists, is chained (``raise ... from``).
    """

    def __init__(self, message: str, records: tuple[PollCycleResult, ...] = ()) -> None:
        super().__init__(message)
        self.records = records


class PollOutcome(StrEnum):
    """The terminal classification of one scheduled attempt."""

    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class PollCycleResult:
    """One observable poll-loop event (pure reporting; never a secret)."""

    started_at: datetime
    finished_at: datetime
    outcome: PollOutcome
    scheduled_for: datetime | None = None
    late_seconds: float = 0.0
    report: CycleReport | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        for name, value in (("started_at", self.started_at), ("finished_at", self.finished_at)):
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise LivePollLoopError(f"{name} must be a timezone-aware datetime")
        if self.finished_at < self.started_at:
            raise LivePollLoopError("finished_at must not precede started_at")
        if not isinstance(self.outcome, PollOutcome):
            raise LivePollLoopError("outcome must be a PollOutcome")
        if self.scheduled_for is not None and (
            not isinstance(self.scheduled_for, datetime) or self.scheduled_for.utcoffset() is None
        ):
            raise LivePollLoopError("scheduled_for must be a timezone-aware datetime or None")
        if type(self.late_seconds) is not float or self.late_seconds < 0.0:
            raise LivePollLoopError("late_seconds must be a nonnegative float")
        if self.error is not None and not isinstance(self.error, str):
            raise LivePollLoopError("error must be a string or None")
        if self.outcome is PollOutcome.SUCCESS and self.report is None:
            raise LivePollLoopError("a successful attempt must carry its CycleReport")
        if self.outcome is not PollOutcome.SUCCESS and self.report is not None:
            raise LivePollLoopError("only a successful attempt carries a CycleReport")


@dataclass(frozen=True, slots=True)
class LivePollLoopConfig:
    """Strict, scheduling-only settings; nothing here reaches analysis."""

    jitter_seconds: float = DEFAULT_POLL_JITTER_SECONDS
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS
    sleep_chunk_seconds: float = DEFAULT_SLEEP_CHUNK_SECONDS

    def __post_init__(self) -> None:
        for name in (
            "jitter_seconds",
            "backoff_base_seconds",
            "backoff_max_seconds",
            "sleep_chunk_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise LivePollLoopError(f"{name} must be a finite number")
        if self.jitter_seconds < 0:
            raise LivePollLoopError("jitter_seconds must be nonnegative")
        if self.backoff_base_seconds <= 0:
            raise LivePollLoopError("backoff_base_seconds must be positive")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise LivePollLoopError("backoff_max_seconds must be at least backoff_base_seconds")
        if self.sleep_chunk_seconds <= 0:
            raise LivePollLoopError("sleep_chunk_seconds must be positive")
        object.__setattr__(self, "jitter_seconds", float(self.jitter_seconds))
        object.__setattr__(self, "backoff_base_seconds", float(self.backoff_base_seconds))
        object.__setattr__(self, "backoff_max_seconds", float(self.backoff_max_seconds))
        object.__setattr__(self, "sleep_chunk_seconds", float(self.sleep_chunk_seconds))


def _finite_env_number(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise LivePollLoopError(f"{name} must be a finite number, got {raw!r}") from exc
    if not math.isfinite(value):
        raise LivePollLoopError(f"{name} must be a finite number, got {raw!r}")
    return value


def load_poll_loop_config(source: Mapping[str, str] | None = None) -> LivePollLoopConfig:
    """Load the strict LIVE_POLL_* scheduler settings (standard library only).

    Unknown environment entries are ignored — exactly like every other live
    configuration loader, this reads only its own names and validates them
    strictly. No secret ever participates in scheduler configuration.
    """

    env = os.environ if source is None else source
    return LivePollLoopConfig(
        jitter_seconds=_finite_env_number(env, _POLL_JITTER, DEFAULT_POLL_JITTER_SECONDS),
        backoff_base_seconds=_finite_env_number(
            env, _POLL_BACKOFF_BASE, DEFAULT_BACKOFF_BASE_SECONDS
        ),
        backoff_max_seconds=_finite_env_number(env, _POLL_BACKOFF_MAX, DEFAULT_BACKOFF_MAX_SECONDS),
    )


def _require_aware(now: datetime) -> None:
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise LivePollLoopError("now must be a timezone-aware datetime")
    if now < _EPOCH:
        raise LivePollLoopError("now must not precede the Unix epoch")


def next_poll_time(
    now: datetime,
    timeframe: str,
    jitter_seconds: float = 0.0,
    random_fn: Callable[[], float] | None = None,
) -> datetime:
    """The next candle-boundary poll time at or after ``now`` (pure function).

    Exact grid arithmetic: the boundary is ``now`` when ``now`` sits exactly
    on one, otherwise the smallest boundary strictly after ``now`` — computed
    with ``timedelta`` remainder math against the Unix epoch so repeated
    schedules never drift. ``jitter_seconds`` scales the injected
    ``random_fn`` output (expected in ``[0.0, 1.0]``) into an additive delay,
    so the scheduled time can only be later than the boundary, never earlier.
    A nonzero jitter without an injected random source is refused: the loop
    never falls back to uncontrolled global randomness.
    """

    _require_aware(now)
    step = timedelta(seconds=timeframe_seconds(timeframe))
    elapsed = now - _EPOCH
    remainder = elapsed % step
    # Always strictly after ``now``: a poll at boundary B has already consumed
    # every candle closed by B, so the next boundary is B + one step. This
    # keeps repeated scheduling drift-free and spin-free even when a cycle
    # finishes exactly on a boundary.
    boundary = now + (step - remainder)
    if jitter_seconds:
        if random_fn is None:
            raise LivePollLoopError(
                "jitter_seconds requires an injected random_fn; "
                "uncontrolled global randomness is never used"
            )
        share = float(jitter_seconds) * float(random_fn())
        if math.isfinite(share) and share > 0.0:
            boundary = boundary + timedelta(seconds=share)
    return boundary


class CycleRunner(Protocol):
    """The one capability the loop consumes: the public service cycle."""

    def run_cycle(self) -> CycleReport:
        """Run one existing Phase 33 poll cycle and report its summary."""
        ...


_RECOVERABLE_ERRORS = (LiveFeedError, DataProviderError, DataValidationError)
_FATAL_ERRORS = (LiveConfigurationError, DataConfigurationError, AnalysisInputError)


class LivePollLoop:
    """Drive ``run_cycle()`` on absolute candle boundaries until stopped.

    The loop is a plain synchronous object: an injected clock, sleep, and
    random source make every timing decision deterministic and testable, no
    thread or daemon is created, no global state exists, and importing this
    module starts nothing. Shutdown is prompt: waits are split into bounded
    chunks and each chunk is followed by a stop check, so ``request_stop()``
    is honored within one chunk instead of a whole candle, while the current
    synchronous cycle always runs to completion (its persistence stays
    atomic). The loop persists nothing itself.
    """

    def __init__(
        self,
        service: CycleRunner,
        *,
        config: LiveServiceConfig,
        poll_config: LivePollLoopConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        if not callable(getattr(service, "run_cycle", None)):
            raise LivePollLoopError("the poll loop requires a service with run_cycle()")
        if not isinstance(config, LiveServiceConfig):
            raise LivePollLoopError("the poll loop requires a LiveServiceConfig")
        if poll_config is not None and not isinstance(poll_config, LivePollLoopConfig):
            raise LivePollLoopError("poll_config must be a LivePollLoopConfig or None")
        resolved_clock = clock if clock is not None else _system_clock
        probe = resolved_clock()
        _require_aware(probe)
        self._service = service
        self._config = config
        self._poll = poll_config if poll_config is not None else LivePollLoopConfig()
        self._clock = resolved_clock
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._random = random_fn if random_fn is not None else random.random
        self._stop = threading.Event()
        self._records: list[PollCycleResult] = []

    # -- lifecycle ---------------------------------------------------------------

    def request_stop(self) -> None:
        """Ask the loop to stop; the current cycle still finishes safely."""

        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        """True once a shutdown has been requested."""

        return self._stop.is_set()

    @property
    def records(self) -> tuple[PollCycleResult, ...]:
        """Every recorded attempt so far, in order (read-only copy)."""

        return tuple(self._records)

    @property
    def timeframe(self) -> str:
        """The primary timeframe the schedule aligns to (from the service config)."""

        return self._config.timeframe

    def run(self) -> tuple[PollCycleResult, ...]:
        """Poll on candle boundaries until stopped or a fatal error stops it.

        Returns every attempt record. Recoverable failures back off
        exponentially (bounded, reset by success) and retry the cycle;
        boundary alignment always resumes from the absolute grid after
        success, so no cumulative drift is possible. Fatal conditions raise
        :class:`LivePollLoopError` with the records attached.
        """

        failures = 0
        while not self._stop.is_set():
            target: datetime | None = None
            if failures == 0:
                target = next_poll_time(
                    self._clock(),
                    self._config.timeframe,
                    self._poll.jitter_seconds,
                    self._random,
                )
                if not self._wait_until(target):
                    break
            started = self._clock()
            late = max(0.0, (started - target).total_seconds()) if target is not None else 0.0
            try:
                report = self._service.run_cycle()
            except _FATAL_ERRORS as exc:
                self._record(
                    started, PollOutcome.FATAL, target, late, None, f"{type(exc).__name__}: {exc}"
                )
                raise LivePollLoopError(
                    f"live poll loop stopped: {type(exc).__name__}: {exc}",
                    self.records,
                ) from exc
            except _RECOVERABLE_ERRORS as exc:
                failures += 1
                self._record(
                    started,
                    PollOutcome.RETRYABLE_FAILURE,
                    target,
                    late,
                    None,
                    f"{type(exc).__name__}: {exc}",
                )
                if not self._sleep_backoff(self._backoff_seconds(failures)):
                    break
                continue
            except Exception as exc:  # unexpected: never healed internally
                self._record(
                    started, PollOutcome.FATAL, target, late, None, f"{type(exc).__name__}: {exc}"
                )
                raise LivePollLoopError(
                    f"live poll loop stopped: {type(exc).__name__}: {exc}",
                    self.records,
                ) from exc
            self._record(started, PollOutcome.SUCCESS, target, late, report, None)
            failures = 0
        return tuple(self._records)

    # -- internals ----------------------------------------------------------------

    def _record(
        self,
        started: datetime,
        outcome: PollOutcome,
        target: datetime | None,
        late: float,
        report: CycleReport | None,
        error: str | None,
    ) -> PollCycleResult:
        result = PollCycleResult(
            started_at=started,
            finished_at=self._clock(),
            outcome=outcome,
            scheduled_for=target,
            late_seconds=late,
            report=report,
            error=error,
        )
        self._records.append(result)
        return result

    def _wait_until(self, target: datetime) -> bool:
        """Sleep in bounded chunks until ``target``; False when stop is asked."""

        while not self._stop.is_set():
            remaining = (target - self._clock()).total_seconds()
            if remaining <= 0.0:
                return True
            self._sleep(min(self._poll.sleep_chunk_seconds, remaining))
        return False

    def _backoff_seconds(self, failures: int) -> float:
        raw: float = self._poll.backoff_base_seconds * (2 ** (failures - 1))
        return min(raw, self._poll.backoff_max_seconds)

    def _sleep_backoff(self, delay: float) -> bool:
        """Sleep a bounded backoff in chunks; False when stop is asked."""

        waited = 0.0
        while not self._stop.is_set():
            remaining = delay - waited
            if remaining <= 0.0:
                return True
            chunk = min(self._poll.sleep_chunk_seconds, remaining)
            self._sleep(chunk)
            waited += chunk
        return False


def _system_clock() -> datetime:
    return datetime.now(UTC)


def request_stop_on_signals(
    loop: LivePollLoop,
    signals: Sequence[signal.Signals] = (signal.SIGINT, signal.SIGTERM),
) -> Callable[[int, object], None]:
    """Optionally wire SIGINT/SIGTERM to ``loop.request_stop()`` (main thread).

    The handler does exactly one thing — sets the stop flag — and performs no
    network call, no persistence, no Telegram action, and no analysis-state
    change; the loop itself owns shutdown completion. Returns the installed
    handler so tests and operators can inspect or invoke it directly.
    """

    def handler(signum: int, frame: object) -> None:
        loop.request_stop()

    for sig in signals:
        signal.signal(sig, handler)
    return handler
