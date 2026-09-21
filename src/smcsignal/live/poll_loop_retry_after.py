"""Phase 35C remediation: Retry-After handling outside frozen poll_loop.

This module provides additive Retry-After support without modifying
src/smcsignal/live/poll_loop.py (which must remain byte-identical to Phase 35B).

Architecture:
- _extract_retry_after_delay walks exception chain for retry_after_seconds
  and retry_after str, using parse_retry_after for HTTP-date.
- RetryAfterAwarePollLoopConfig subclasses LivePollLoopConfig (frozen) and
  overrides backoff_seconds to return max(base, retry_after) when a holder
  contains a server-directed delay.
- RetryAfterAwareRunner wraps any CycleRunner, extracts retry delay on failure
  and stores it into the shared holder, clears holder on success.
- RetryAfterAwareLivePollLoop subclasses LivePollLoop, composes the above,
  so effective delay = max(backoff, retry_after) while frozen poll_loop.py
  remains untouched.

No second scheduler, no duplication of poll loop logic beyond one method
override for backoff, stdlib only, no network.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from smcsignal.live.poll_loop import (
    CycleRunner,
    LivePollLoop,
    LivePollLoopConfig,
    PollCycleResult,
)
from smcsignal.live.retry_after import (
    MAX_RETRY_AFTER_SECONDS,
    parse_retry_after,
)
from smcsignal.live.service import CycleReport

Clock = Callable[[], datetime]
SleepFn = Callable[[float], None]
RandomSource = Callable[[], float]
OnCycle = Callable[[PollCycleResult], None]


def _extract_retry_after_delay(
    error: BaseException, *, now: datetime | None = None
) -> float | None:
    """Extract server-directed retry delay from exception chain.

    Looks for retry_after_seconds (float) and retry_after (str).
    Never raises.
    """

    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        exc = stack.pop()
        if id(exc) in seen:
            continue
        seen.add(id(exc))

        retry_after_seconds = getattr(exc, "retry_after_seconds", None)
        if isinstance(retry_after_seconds, (int, float)) and not isinstance(
            retry_after_seconds, bool
        ):
            try:
                val = float(retry_after_seconds)
                if math.isfinite(val) and val >= 0:
                    return min(val, float(MAX_RETRY_AFTER_SECONDS))
            except (ValueError, TypeError):
                pass

        retry_after_raw = getattr(exc, "retry_after", None)
        if isinstance(retry_after_raw, str):
            try:
                parsed = parse_retry_after(retry_after_raw, now=now)
                if parsed is not None:
                    return parsed
            except Exception:
                pass

        cause = getattr(exc, "__cause__", None)
        context = getattr(exc, "__context__", None)
        if isinstance(cause, BaseException):
            stack.append(cause)
        if isinstance(context, BaseException):
            stack.append(context)

    return None


@dataclass(frozen=True, slots=True)
class RetryAfterAwarePollLoopConfig(LivePollLoopConfig):
    """LivePollLoopConfig that honors server Retry-After as lower bound.

    Holds a mutable holder list [float|None] shared with runner wrapper.
    backoff_seconds returns max(base, holder[0]) when holder contains valid delay.
    """

    _retry_after_holder: list[float | None] = field(
        default_factory=lambda: [None], init=False, repr=False, compare=False
    )

    def backoff_seconds(self, consecutive_failures: int) -> float:
        # Call parent implementation directly to avoid super() issues with frozen slots
        base = LivePollLoopConfig.backoff_seconds(self, consecutive_failures)
        try:
            holder = self._retry_after_holder
            ra = holder[0] if holder else None
            if isinstance(ra, (int, float)) and not isinstance(ra, bool):
                raf = float(ra)
                if math.isfinite(raf) and raf >= 0:
                    return max(base, raf)
        except Exception:
            pass
        return base

    def _set_retry_after(self, value: float | None) -> None:
        try:
            self._retry_after_holder[0] = value
        except Exception:
            pass

    def _clear_retry_after(self) -> None:
        try:
            self._retry_after_holder[0] = None
        except Exception:
            pass


class RetryAfterAwareRunner:
    """Wraps a CycleRunner to propagate Retry-After into config holder."""

    def __init__(
        self,
        runner: CycleRunner,
        holder: list[float | None],
        clock: Clock | None = None,
    ) -> None:
        self._runner = runner
        self._holder = holder
        self._clock = clock

    def run_cycle(self) -> CycleReport:
        try:
            result = self._runner.run_cycle()
            # success → clear holder
            try:
                self._holder[0] = None
            except Exception:
                pass
            return result
        except BaseException as exc:
            # extract delay using clock now if available
            now = None
            if self._clock is not None:
                try:
                    now = self._clock()
                except Exception:
                    now = None
            if now is None:
                now = datetime.now(UTC)
            try:
                delay = _extract_retry_after_delay(exc, now=now)
            except Exception:
                delay = None
            try:
                self._holder[0] = delay
            except Exception:
                pass
            raise


class RetryAfterAwareLivePollLoop(LivePollLoop):
    """Additive wrapper over frozen LivePollLoop that honors Retry-After.

    Composition:
    - Creates shared holder [None]
    - Wraps config into RetryAfterAwarePollLoopConfig (same values + holder)
    - Wraps runner into RetryAfterAwareRunner (holder + clock)
    - Delegates all scheduling to frozen LivePollLoop; only backoff_seconds
      is overridden to return max(backoff, retry_after).

    No second scheduler, no duplication of loop logic.
    """

    def __init__(
        self,
        runner: CycleRunner,
        config: LivePollLoopConfig,
        *,
        clock: Clock | None = None,
        sleep_fn: SleepFn | None = None,
        random_source: RandomSource | None = None,
        on_cycle: OnCycle | None = None,
        history_limit: int = 256,
    ) -> None:
        holder: list[float | None] = [None]

        # Build retry-aware config from base config values
        # Preserve all validated fields, add holder
        aware_config = RetryAfterAwarePollLoopConfig(
            timeframe=config.timeframe,
            jitter_min_seconds=config.jitter_min_seconds,
            jitter_max_seconds=config.jitter_max_seconds,
            backoff_base_seconds=config.backoff_base_seconds,
            backoff_max_seconds=config.backoff_max_seconds,
            max_consecutive_failures=config.max_consecutive_failures,
            wait_chunk_seconds=config.wait_chunk_seconds,
        )
        # Inject holder (mutate list object, not attribute reassign)
        try:
            aware_config._retry_after_holder[0] = None
            # Replace holder reference with shared holder
            # Since frozen, we need to mutate the list object itself to share?
            # Instead, we will make aware_config's holder be the shared holder
            # by mutating its list to be same object via object.__setattr__ bypass
            object.__setattr__(aware_config, "_retry_after_holder", holder)
        except Exception:
            # Fallback: try to set via object.__setattr__
            try:
                object.__setattr__(aware_config, "_retry_after_holder", holder)
            except Exception:
                pass

        wrapped_runner = RetryAfterAwareRunner(runner, holder, clock=clock)

        super().__init__(
            wrapped_runner,
            aware_config,
            clock=clock,
            sleep_fn=sleep_fn,
            random_source=random_source,
            on_cycle=on_cycle,
            history_limit=history_limit,
        )
        # Keep references for introspection
        self._retry_after_holder = holder
        self._inner_runner = runner
