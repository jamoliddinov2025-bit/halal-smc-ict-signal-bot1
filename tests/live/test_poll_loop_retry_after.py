"""Phase 35C: poll loop Retry-After integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from smcsignal.live import LiveFeedError, LivePollLoop, LivePollLoopConfig
from smcsignal.live.retry_after import RateLimitedFeedError


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.now

    def advance(self, seconds: float):
        self.now = self.now + timedelta(seconds=seconds)


def test_retry_after_shorter_than_backoff_uses_backoff():
    # backoff 10, retry-after 2 → effective 10
    start = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    clock = FakeClock(start)

    class Runner:
        def __init__(self):
            self.calls = 0

        def run_cycle(self):
            self.calls += 1
            if self.calls == 1:
                raise RateLimitedFeedError(
                    status_code=429, retry_after="2", retry_after_seconds=2.0
                )
            from smcsignal.live.service import CycleReport

            return CycleReport(
                primary_candles=0,
                higher_candles=0,
                frames=0,
                buy_signals=0,
                delivery_states=(),
                ledger_persisted=False,
            )

    runner = Runner()
    config = LivePollLoopConfig(
        timeframe="15m", backoff_base_seconds=10.0, backoff_max_seconds=60.0
    )
    sleeps = []

    def sleep_fn(s):
        sleeps.append(s)
        clock.advance(s)

    loop = LivePollLoop(runner, config, clock=clock, sleep_fn=sleep_fn, random_source=lambda: 0.0)
    # First cycle: failure, backoff 10, retry-after 2 → effective 10
    # Second cycle: success, then boundary
    loop.run(max_cycles=2)
    # Check that next_poll_at after first failure was start + 10 (or close)
    # The history[0] is first failure, its next_poll_at should be start + 10
    first_result = loop.history[0]
    assert first_result.outcome.value == "recoverable_failure"
    assert first_result.next_poll_at is not None
    delay = (first_result.next_poll_at - first_result.finished_at).total_seconds()
    # Effective should be max(10,2)=10
    assert abs(delay - 10.0) < 0.5


def test_retry_after_longer_than_backoff_uses_retry_after():
    start = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    clock = FakeClock(start)

    class Runner:
        def __init__(self):
            self.calls = 0

        def run_cycle(self):
            self.calls += 1
            if self.calls == 1:
                raise RateLimitedFeedError(
                    status_code=429, retry_after="30", retry_after_seconds=30.0
                )
            from smcsignal.live.service import CycleReport

            return CycleReport(
                primary_candles=0,
                higher_candles=0,
                frames=0,
                buy_signals=0,
                delivery_states=(),
                ledger_persisted=False,
            )

    runner = Runner()
    config = LivePollLoopConfig(timeframe="15m", backoff_base_seconds=1.0, backoff_max_seconds=60.0)
    sleeps = []

    def sleep_fn(s):
        sleeps.append(s)
        clock.advance(s)

    loop = LivePollLoop(runner, config, clock=clock, sleep_fn=sleep_fn, random_source=lambda: 0.0)
    loop.run(max_cycles=2)
    first_result = loop.history[0]
    delay = (first_result.next_poll_at - first_result.finished_at).total_seconds()
    assert abs(delay - 30.0) < 0.5


def test_shutdown_interrupts_retry_after_wait():
    start = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    clock = FakeClock(start)

    class Runner:
        def run_cycle(self):
            raise RateLimitedFeedError(status_code=429, retry_after="60", retry_after_seconds=60.0)

    runner = Runner()
    config = LivePollLoopConfig(timeframe="15m", backoff_base_seconds=1.0, backoff_max_seconds=60.0)

    loop = LivePollLoop(
        runner, config, clock=clock, sleep_fn=lambda s: clock.advance(s), random_source=lambda: 0.0
    )

    # Request stop before run, so _wait_until returns False immediately
    loop.request_stop("test")
    executed = loop.run(max_cycles=5)
    assert executed == 0
    assert loop.stop_requested


def test_absent_retry_after_uses_backoff():
    start = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    clock = FakeClock(start)

    class Runner:
        def __init__(self):
            self.calls = 0

        def run_cycle(self):
            self.calls += 1
            if self.calls == 1:
                raise LiveFeedError("generic failure without retry-after")
            from smcsignal.live.service import CycleReport

            return CycleReport(
                primary_candles=0,
                higher_candles=0,
                frames=0,
                buy_signals=0,
                delivery_states=(),
                ledger_persisted=False,
            )

    runner = Runner()
    config = LivePollLoopConfig(timeframe="15m", backoff_base_seconds=5.0, backoff_max_seconds=60.0)

    def sleep_fn(s):
        clock.advance(s)

    loop = LivePollLoop(runner, config, clock=clock, sleep_fn=sleep_fn, random_source=lambda: 0.0)
    loop.run(max_cycles=2)
    first_result = loop.history[0]
    delay = (first_result.next_poll_at - first_result.finished_at).total_seconds()
    assert abs(delay - 5.0) < 0.5
