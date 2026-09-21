"""Phase 35A poll-loop tests: boundary scheduling, jitter, backoff, shutdown.

Every case is deterministic and network-free: an injected fake clock, fake
sleep, fake random source, and a fake cycle runner stand in for the real
service; the pytest process itself forbids socket access.
"""

from __future__ import annotations

import ast
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.data.errors import (
    DataConfigurationError,
    DataProviderError,
    DataValidationError,
    RateLimitError,
)
from smcsignal.live import (
    CycleReport,
    LivePollLoop,
    LivePollLoopConfig,
    LivePollLoopError,
    LiveServiceConfig,
    PollOutcome,
    load_poll_loop_config,
    next_poll_time,
    request_stop_on_signals,
)
from smcsignal.live.config import LiveConfigurationError
from smcsignal.live.market_feed import LiveFeedError

START = datetime(2024, 1, 2, 12, 7, 31, tzinfo=UTC)
BOUNDARY = datetime(2024, 1, 2, 12, 15, tzinfo=UTC)


def service_config(timeframe: str = "15m") -> LiveServiceConfig:
    return LiveServiceConfig(
        symbol="BTCUSDT",
        timeframe=timeframe,
        higher_timeframes=("1h",),
        provider="binance_public",
        history_limit=500,
        csv_path=None,
        enabled=False,
        dry_run=True,
    )


def report() -> CycleReport:
    return CycleReport(
        primary_candles=0,
        higher_candles=0,
        frames=0,
        buy_signals=0,
        delivery_states=(),
        ledger_persisted=False,
    )


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeSleeper:
    """Records sleep amounts and advances the fake clock by each one."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        loop: LivePollLoop | None = None,
        stop_on_call: int | None = None,
        overshoot: float = 0.0,
        stop_below: float | None = None,
    ) -> None:
        self.clock = clock
        self.loop = loop
        self.stop_on_call = stop_on_call
        self.overshoot = overshoot
        self.stop_below = stop_below
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(round(seconds, 6))
        self.clock.advance(seconds + self.overshoot)
        if self.stop_on_call is not None and len(self.calls) >= self.stop_on_call:
            assert self.loop is not None
            self.loop.request_stop()
        if self.stop_below is not None and seconds < self.stop_below:
            assert self.loop is not None
            self.loop.request_stop()
        if self.stop_below is not None and seconds < self.stop_below:
            assert self.loop is not None
            self.loop.request_stop()


class FakeService:
    """Scripted cycle runner: one outcome per call (the last one repeats)."""

    def __init__(
        self,
        outcomes: list[CycleReport | Exception],
        *,
        loop: LivePollLoop | None = None,
        stop_after: int | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.loop = loop
        self.stop_after = stop_after
        self.calls = 0

    def run_cycle(self) -> CycleReport:
        self.calls += 1
        if self.stop_after is not None and self.calls >= self.stop_after:
            assert self.loop is not None
            self.loop.request_stop()
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_loop(
    outcomes: list[CycleReport | Exception],
    *,
    start: datetime = START,
    timeframe: str = "15m",
    stop_after: int | None = None,
    poll_config: LivePollLoopConfig | None = None,
    random_fn: Callable[[], float] | None = None,
    sleep_chunk_seconds: float = 1000.0,
    overshoot: float = 0.0,
    sleeper: FakeSleeper | None = None,
    clock: FakeClock | None = None,
) -> tuple[LivePollLoop, FakeService, FakeSleeper, FakeClock]:
    """One wired (loop, service, sleeper, clock) with the stop hook attached."""

    config = (
        poll_config
        if poll_config is not None
        else LivePollLoopConfig(sleep_chunk_seconds=sleep_chunk_seconds)
    )
    resolved_clock = clock if clock is not None else FakeClock(start)
    resolved_sleeper = (
        sleeper if sleeper is not None else FakeSleeper(resolved_clock, overshoot=overshoot)
    )
    service = FakeService(outcomes)
    loop = LivePollLoop(
        service,
        config=service_config(timeframe),
        poll_config=config,
        clock=resolved_clock,
        sleep_fn=resolved_sleeper,
        random_fn=random_fn,
    )
    service.loop = loop
    if stop_after is not None:
        service.stop_after = stop_after
    return loop, service, resolved_sleeper, resolved_clock


class TestScheduling:
    def test_first_poll_is_the_next_boundary_after_a_mid_candle_start(self) -> None:
        loop, service, _, _ = make_loop([report()], stop_after=1)
        records = loop.run()
        assert [r.scheduled_for for r in records] == [BOUNDARY]
        assert service.calls == 1
        assert records[0].late_seconds == 0.0
        assert records[0].outcome is PollOutcome.SUCCESS
        assert records[0].report is not None

    def test_start_one_second_after_a_boundary_skips_to_the_next(self) -> None:
        loop, _, _, _ = make_loop(
            [report()], start=datetime(2024, 1, 2, 12, 0, 1, tzinfo=UTC), stop_after=1
        )
        assert [r.scheduled_for for r in loop.run()] == [BOUNDARY]

    def test_start_exactly_on_a_boundary_waits_for_the_next_one(self) -> None:
        # Spin safety: a poll at boundary B consumes every candle closed by B,
        # so the next boundary is B + one step — never B again.
        loop, _, _, _ = make_loop([report()], start=BOUNDARY, stop_after=3)
        records = loop.run()
        assert [r.scheduled_for for r in records] == [
            datetime(2024, 1, 2, 12, 30, tzinfo=UTC),
            datetime(2024, 1, 2, 12, 45, tzinfo=UTC),
            datetime(2024, 1, 2, 13, 0, tzinfo=UTC),
        ]

    def test_start_just_before_a_boundary_polls_at_that_boundary(self) -> None:
        start = datetime(2024, 1, 2, 11, 59, 59, 999999, tzinfo=UTC)
        loop, _, _, _ = make_loop([report()], start=start, stop_after=2)
        records = loop.run()
        assert records[0].scheduled_for == datetime(2024, 1, 2, 12, 0, tzinfo=UTC)
        assert records[1].scheduled_for == datetime(2024, 1, 2, 12, 15, tzinfo=UTC)

    def test_no_cumulative_drift_across_cycles(self) -> None:
        loop, _, sleeper, _ = make_loop(
            [report()], start=BOUNDARY, stop_after=4, sleep_chunk_seconds=1000.0
        )
        records = loop.run()
        assert [r.scheduled_for.strftime("%H:%M") for r in records] == [
            "12:30",
            "12:45",
            "13:00",
            "13:15",
        ]
        # Instant cycles: every wait is exactly one full timeframe interval.
        assert sleeper.calls == [900.0, 900.0, 900.0, 900.0]

    def test_late_wake_is_recorded(self) -> None:
        loop, _, _, _ = make_loop(
            [report()], stop_after=1, sleep_chunk_seconds=1000.0, overshoot=61.0
        )
        records = loop.run()
        assert records[0].late_seconds == 61.0
        assert records[0].scheduled_for == BOUNDARY

    @pytest.mark.parametrize(
        ("timeframe", "want"),
        [
            ("1m", "12:08"),
            ("5m", "12:10"),
            ("15m", "12:15"),
            ("1h", "13:00"),
            ("4h", "16:00"),
        ],
    )
    def test_next_poll_time_grid_for_representative_timeframes(
        self, timeframe: str, want: str
    ) -> None:
        assert next_poll_time(START, timeframe).strftime("%H:%M") == want

    def test_next_poll_time_rejects_naive_and_pre_epoch_instants(self) -> None:
        with pytest.raises(LivePollLoopError, match="timezone-aware"):
            next_poll_time(datetime(2024, 1, 2, 12, 0, 0), "15m")
        with pytest.raises(LivePollLoopError, match="Unix epoch"):
            next_poll_time(datetime(1969, 1, 1, tzinfo=UTC), "15m")


class TestJitter:
    def test_jitter_scales_the_injected_random_share(self) -> None:
        loop, _, _, _ = make_loop(
            [report()],
            stop_after=1,
            poll_config=LivePollLoopConfig(jitter_seconds=2.0, sleep_chunk_seconds=1000.0),
            random_fn=lambda: 0.37,
        )
        records = loop.run()
        assert records[0].scheduled_for == BOUNDARY + timedelta(seconds=0.74)

    def test_minimum_jitter_keeps_the_boundary(self) -> None:
        loop, _, _, _ = make_loop(
            [report()],
            stop_after=1,
            poll_config=LivePollLoopConfig(jitter_seconds=5.0, sleep_chunk_seconds=1000.0),
            random_fn=lambda: 0.0,
        )
        assert loop.run()[0].scheduled_for == BOUNDARY

    def test_maximum_jitter_never_exceeds_the_bound(self) -> None:
        loop, _, _, _ = make_loop(
            [report()],
            stop_after=1,
            poll_config=LivePollLoopConfig(jitter_seconds=5.0, sleep_chunk_seconds=1000.0),
            random_fn=lambda: 1.0,
        )
        assert loop.run()[0].scheduled_for == BOUNDARY + timedelta(seconds=5.0)

    @pytest.mark.parametrize("share", [0.0, 0.1, 0.5, 0.9, 1.0])
    def test_jitter_never_schedules_before_the_boundary(self, share: float) -> None:
        assert next_poll_time(START, "15m", 5.0, lambda: share) >= BOUNDARY

    def test_jitter_requires_an_injected_random_source(self) -> None:
        with pytest.raises(LivePollLoopError, match="injected random_fn"):
            next_poll_time(START, "15m", 5.0)

    def test_deterministic_jitter_sequence(self) -> None:
        shares = iter([0.1, 0.2])
        first = next_poll_time(START, "15m", 5.0, lambda: next(shares))
        second = next_poll_time(START, "15m", 5.0, lambda: next(shares))
        assert second - first == timedelta(seconds=0.5)


class TestBackoff:
    def test_recoverable_failure_applies_exponential_backoff(self) -> None:
        loop, service, sleeper, _ = make_loop(
            [LiveFeedError("transport down"), LiveFeedError("still down"), report()],
            stop_after=3,
            poll_config=LivePollLoopConfig(backoff_base_seconds=2.0, sleep_chunk_seconds=1000.0),
        )
        records = loop.run()
        assert [r.outcome for r in records] == [
            PollOutcome.RETRYABLE_FAILURE,
            PollOutcome.RETRYABLE_FAILURE,
            PollOutcome.SUCCESS,
        ]
        # 449 s is the exact 12:07:31 -> 12:15 boundary span, then the backoffs.
        assert sleeper.calls == [449.0, 2.0, 4.0]
        assert service.calls == 3
        assert records[0].error is not None and "LiveFeedError" in records[0].error
        assert records[1].scheduled_for is None  # backoff retries are not boundary polls

    def test_backoff_is_capped_at_the_maximum(self) -> None:
        outcomes: list[CycleReport | Exception] = [LiveFeedError("down")] * 5 + [report()]
        loop, _, sleeper, _ = make_loop(
            outcomes,
            stop_after=6,
            poll_config=LivePollLoopConfig(
                backoff_base_seconds=2.0,
                backoff_max_seconds=6.0,
                sleep_chunk_seconds=1000.0,
            ),
        )
        records = loop.run()
        assert [r.outcome for r in records[:5]] == [PollOutcome.RETRYABLE_FAILURE] * 5
        assert sleeper.calls == [449.0, 2.0, 4.0, 6.0, 6.0, 6.0]

    def test_success_resets_the_failure_counter(self) -> None:
        loop, _, sleeper, _ = make_loop(
            [LiveFeedError("x"), report(), LiveFeedError("y"), report()],
            stop_after=4,
            poll_config=LivePollLoopConfig(backoff_base_seconds=2.0, sleep_chunk_seconds=1000.0),
        )
        records = loop.run()
        assert [r.outcome for r in records] == [
            PollOutcome.RETRYABLE_FAILURE,
            PollOutcome.SUCCESS,
            PollOutcome.RETRYABLE_FAILURE,
            PollOutcome.SUCCESS,
        ]
        # 449 s boundary wait, backoff, 894 s boundary wait (12:15:02 -> 12:30),
        # backoff: the second failure starts from base again, never the old count.
        assert sleeper.calls == [449.0, 2.0, 898.0, 2.0]
        # The second failure starts from base again, never from the old count.

    def test_recovery_would_resume_absolute_boundaries(self) -> None:
        loop, _, _, clock = make_loop(
            [LiveFeedError("x"), report()],
            stop_after=2,
            poll_config=LivePollLoopConfig(backoff_base_seconds=30.0, sleep_chunk_seconds=1000.0),
        )
        records = loop.run()
        # Failure at the 12:15 boundary, backoff to 12:15:30, success there;
        # the next boundary remains the absolute 12:30 grid point.
        assert records[0].scheduled_for == BOUNDARY
        assert records[1].finished_at == datetime(2024, 1, 2, 12, 15, 30, tzinfo=UTC)
        assert clock.now == datetime(2024, 1, 2, 12, 15, 30, tzinfo=UTC)

    @pytest.mark.parametrize(
        "error",
        [
            LiveFeedError("retry budget exhausted"),
            DataProviderError("transport failed"),
            RateLimitError(429, "10"),
            DataValidationError("malformed kline page"),
        ],
        ids=["feed-exhausted", "provider", "rate-limit", "malformed"],
    )
    def test_market_data_failures_are_recoverable(self, error: Exception) -> None:
        loop, service, _, _ = make_loop([error, report()], stop_after=2)
        records = loop.run()
        assert [r.outcome for r in records] == [
            PollOutcome.RETRYABLE_FAILURE,
            PollOutcome.SUCCESS,
        ]
        assert service.calls == 2

    def test_configuration_error_stops_immediately(self) -> None:
        loop, service, _, _ = make_loop([LiveConfigurationError("missing chat id")])
        with pytest.raises(LivePollLoopError, match="LiveConfigurationError") as excinfo:
            loop.run()
        assert isinstance(excinfo.value.__cause__, LiveConfigurationError)
        assert len(excinfo.value.records) == 1
        assert excinfo.value.records[0].outcome is PollOutcome.FATAL
        assert service.calls == 1  # no retry on configuration failure

    def test_corrupt_state_error_stops_the_loop(self) -> None:
        loop, service, _, _ = make_loop([AnalysisInputError("recovery verification failed")])
        with pytest.raises(LivePollLoopError, match="AnalysisInputError"):
            loop.run()
        assert service.calls == 1

    def test_data_configuration_error_is_fatal_not_recoverable(self) -> None:
        loop, service, _, _ = make_loop([DataConfigurationError("bad market data config")])
        with pytest.raises(LivePollLoopError, match="DataConfigurationError"):
            loop.run()
        assert service.calls == 1

    def test_unexpected_error_stops_the_loop(self) -> None:
        loop, service, _, _ = make_loop([RuntimeError("unexpected internal state")])
        with pytest.raises(LivePollLoopError, match="RuntimeError"):
            loop.run()
        assert service.calls == 1


class TestShutdown:
    def test_stop_before_the_first_cycle_runs_nothing(self) -> None:
        loop, service, _, _ = make_loop([report()])
        loop.request_stop()
        assert loop.stop_requested is True
        assert loop.run() == ()
        assert service.calls == 0

    def test_stop_while_waiting_exits_promptly(self) -> None:
        clock = FakeClock(START)
        sleeper = FakeSleeper(clock, stop_on_call=2)
        loop, service, resolved_sleeper, _ = make_loop(
            [report()],
            sleeper=sleeper,
            clock=clock,
            poll_config=LivePollLoopConfig(sleep_chunk_seconds=1.0),
        )
        resolved_sleeper.loop = loop
        records = loop.run()
        assert records == ()
        assert service.calls == 0
        assert resolved_sleeper.calls == [1.0, 1.0]  # two 1-second chunks, not 449

    def test_stop_during_backoff_stops_without_retry(self) -> None:
        clock = FakeClock(START)
        # Whole waits land as 449-second chunks; the 2-second backoff is the
        # first sleep below 300, so the stop fires exactly during backoff.
        sleeper = FakeSleeper(clock, stop_below=300.0)
        loop, service, resolved_sleeper, _ = make_loop(
            [LiveFeedError("down"), report()],
            sleeper=sleeper,
            clock=clock,
            poll_config=LivePollLoopConfig(backoff_base_seconds=2.0, sleep_chunk_seconds=1000.0),
        )
        resolved_sleeper.loop = loop
        service.stop_after = None  # the stop comes from the sleeper, not the service
        records = loop.run()
        assert [r.outcome for r in records] == [PollOutcome.RETRYABLE_FAILURE]
        assert service.calls == 1
        assert resolved_sleeper.calls == [449.0, 2.0]

    def test_stop_inside_a_cycle_lets_it_finish(self) -> None:
        clock = FakeClock(START)

        class StopsInside:
            def __init__(self) -> None:
                self.calls = 0

            def run_cycle(self) -> CycleReport:
                self.calls += 1
                loop.request_stop()
                return report()

        service = StopsInside()
        loop = LivePollLoop(service, config=service_config(), clock=clock, sleep_fn=clock.advance)
        records = loop.run()
        assert service.calls == 1
        assert [r.outcome for r in records] == [PollOutcome.SUCCESS]
        assert loop.stop_requested is True

    def test_waits_are_chunked_so_a_stop_is_observed_promptly(self) -> None:
        clock = FakeClock(START)
        sleeper = FakeSleeper(clock, stop_on_call=2)
        loop, _, resolved_sleeper, _ = make_loop(
            [report()],
            sleeper=sleeper,
            clock=clock,
            poll_config=LivePollLoopConfig(sleep_chunk_seconds=10.0),
        )
        resolved_sleeper.loop = loop
        loop.run()
        assert resolved_sleeper.calls == [10.0, 10.0]  # bounded chunks, prompt exit

    def test_records_are_readable_before_and_after_run(self) -> None:
        loop, _, _, _ = make_loop([report()], stop_after=1)
        assert loop.records == ()
        records = loop.run()
        assert loop.records == tuple(records)
        assert len(records) == 1

    def test_run_returns_cleanly_when_already_stopped(self) -> None:
        loop, service, _, _ = make_loop([report()], stop_after=1)
        loop.run()
        first_len = len(loop.records)
        assert loop.run() == tuple(loop.records)  # no further cycles once stopped
        assert service.calls == 1
        assert len(loop.records) == first_len


class TestRestart:
    def test_loop_holds_no_persistent_state(self) -> None:
        loop, _, _, _ = make_loop([report()], stop_after=1)
        loop.run()
        for banned in ("save", "load", "persist", "store"):
            assert not hasattr(loop, banned)
        assert isinstance(loop.records, tuple)

    def test_a_fresh_loop_resumes_at_the_next_boundary(self) -> None:
        _, _, _, clock = make_loop(
            [report()], start=BOUNDARY, stop_after=2, sleep_chunk_seconds=1000.0
        )
        # First process: two cycles end somewhere past 13:00.
        # (The stop fires during the second cycle; the clock moved one interval.)
        first_loop, _, _, _ = make_loop(
            [report()], start=BOUNDARY, stop_after=2, sleep_chunk_seconds=1000.0
        )
        first_loop.run()
        # Restarted process: a brand-new loop realigns to the absolute grid.
        second_loop, second_service, _, _ = make_loop(
            [report()], start=clock.now, stop_after=1, sleep_chunk_seconds=1000.0
        )
        records = second_loop.run()
        assert second_service.calls == 1
        assert records[0].scheduled_for is not None
        assert records[0].scheduled_for > clock.now - timedelta(seconds=1)
        minute = records[0].scheduled_for.minute
        assert minute % 15 == 0  # aligned to the 15m grid


class TestLoopConstruction:
    def test_loop_requires_a_cycle_runner(self) -> None:
        class NotARunner:
            pass

        with pytest.raises(LivePollLoopError, match="run_cycle"):
            LivePollLoop(NotARunner(), config=service_config(), clock=FakeClock(START))  # type: ignore[arg-type]

    def test_loop_requires_a_live_service_config(self) -> None:
        with pytest.raises(LivePollLoopError, match="LiveServiceConfig"):
            LivePollLoop(FakeService([report()]), config=object(), clock=FakeClock(START))  # type: ignore[arg-type]

    def test_loop_rejects_a_naive_clock(self) -> None:
        with pytest.raises(LivePollLoopError, match="timezone-aware"):
            LivePollLoop(
                FakeService([report()]),
                config=service_config(),
                clock=lambda: datetime(2024, 1, 2, 12, 0, 0),  # naive
            )

    def test_poll_loop_config_validates_strictly(self) -> None:
        with pytest.raises(LivePollLoopError, match="jitter_seconds must be nonnegative"):
            LivePollLoopConfig(jitter_seconds=-1.0)
        with pytest.raises(LivePollLoopError, match="backoff_base_seconds must be positive"):
            LivePollLoopConfig(backoff_base_seconds=0.0)
        with pytest.raises(LivePollLoopError, match="backoff_max_seconds must be at least"):
            LivePollLoopConfig(backoff_base_seconds=5.0, backoff_max_seconds=4.0)
        with pytest.raises(LivePollLoopError, match="must be a finite number"):
            LivePollLoopConfig(jitter_seconds=float("nan"))
        with pytest.raises(LivePollLoopError, match="sleep_chunk_seconds must be positive"):
            LivePollLoopConfig(sleep_chunk_seconds=0.0)

    def test_signal_handler_only_requests_stop(self) -> None:
        loop, _, _, _ = make_loop([report()])
        previous: dict[int, object] = {}
        installed = request_stop_on_signals(loop)
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.getsignal(sig)
            assert loop.stop_requested is False
            installed(int(signal.SIGINT), None)
            assert loop.stop_requested is True
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)  # type: ignore[arg-type]


class TestConfiguration:
    def test_loader_defaults(self) -> None:
        config = load_poll_loop_config({})
        assert config.jitter_seconds == 0.0
        assert config.backoff_base_seconds == 1.0
        assert config.backoff_max_seconds == 30.0

    def test_loader_reads_the_documented_names(self) -> None:
        config = load_poll_loop_config(
            {
                "LIVE_POLL_JITTER_SECONDS": "2.5",
                "LIVE_POLL_BACKOFF_BASE_SECONDS": "0.5",
                "LIVE_POLL_BACKOFF_MAX_SECONDS": "8",
            }
        )
        assert config.jitter_seconds == 2.5
        assert config.backoff_base_seconds == 0.5
        assert config.backoff_max_seconds == 8.0

    def test_empty_values_mean_unset_and_apply_defaults(self) -> None:
        config = load_poll_loop_config({"LIVE_POLL_JITTER_SECONDS": "  "})
        assert config.jitter_seconds == 0.0

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"LIVE_POLL_JITTER_SECONDS": "-1"}, "jitter_seconds must be nonnegative"),
            ({"LIVE_POLL_BACKOFF_BASE_SECONDS": "0"}, "backoff_base_seconds must be positive"),
            (
                {"LIVE_POLL_BACKOFF_BASE_SECONDS": "5", "LIVE_POLL_BACKOFF_MAX_SECONDS": "4"},
                "backoff_max_seconds must be at least",
            ),
            ({"LIVE_POLL_JITTER_SECONDS": "abc"}, "must be a finite number"),
            ({"LIVE_POLL_JITTER_SECONDS": "nan"}, "must be a finite number"),
            ({"LIVE_POLL_BACKOFF_MAX_SECONDS": "inf"}, "must be a finite number"),
        ],
    )
    def test_loader_rejects_invalid_values(self, overrides: dict[str, str], message: str) -> None:
        with pytest.raises(LivePollLoopError, match=message):
            load_poll_loop_config(overrides)

    def test_loader_uses_os_environ_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIVE_POLL_JITTER_SECONDS", "3")
        config = load_poll_loop_config()
        assert config.jitter_seconds == 3.0


class TestIntegrationWithTheRealService:
    def test_the_loop_drives_a_real_live_service(self) -> None:
        from tests.live.test_service import scripted_service

        real_service = scripted_service((6, 6))

        class StopAfterOne:
            """Runs the real cycle once, then asks the loop to stop."""

            def __init__(self) -> None:
                self.calls = 0

            def run_cycle(self) -> CycleReport:
                self.calls += 1
                outcome = real_service.run_cycle()
                loop.request_stop()
                return outcome

        clock = FakeClock(START)
        stopper = StopAfterOne()
        loop = LivePollLoop(
            stopper,
            config=service_config(),
            poll_config=LivePollLoopConfig(sleep_chunk_seconds=1000.0),
            clock=clock,
            sleep_fn=clock.advance,
            random_fn=lambda: 0.0,
        )
        records = loop.run()
        assert len(records) == 1
        assert records[0].outcome is PollOutcome.SUCCESS
        assert records[0].report is not None
        assert records[0].scheduled_for == BOUNDARY
        # The real service processed its warmed history: identical page, so the
        # cycle persisted nothing new — the loop only decided *when* it ran.
        assert records[0].report.frames == 0


class TestArchitecturalGuard:
    """Phase 35A frozen-core guard: scheduling only, no engine, no transport."""

    MODULE = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "live" / "poll_loop.py"

    def test_poll_loop_imports_only_sanctioned_frozen_modules(self) -> None:
        allowed = {
            "smcsignal.analysis.errors",
            "smcsignal.analysis.mtf.timeframes",
            "smcsignal.data.errors",
            "smcsignal.live.config",
            "smcsignal.live.market_feed",
            "smcsignal.live.service",
        }
        tree = ast.parse(self.MODULE.read_text(encoding="utf-8"))
        frozen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                frozen.update(a.name for a in node.names if a.name.startswith("smcsignal"))
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.startswith("smcsignal"):
                    frozen.add(node.module)
        assert frozen <= allowed, frozen - allowed
        assert not any(name.startswith("smcsignal.delivery") for name in frozen)

    def test_poll_loop_never_mentions_engine_or_transport_symbols(self) -> None:
        text = self.MODULE.read_text(encoding="utf-8")
        for banned in (
            "HistoricalReplay",
            "SignalEngineAnalyzer",
            "MTFAnalyzer",
            "TelegramHttpClient",
            "TelegramSink",
            "urlopen",
        ):
            assert banned not in text, banned

    def test_live_package_still_exports_the_loop_api(self) -> None:
        import smcsignal.live as live_package

        for name in (
            "LivePollLoop",
            "LivePollLoopConfig",
            "LivePollLoopError",
            "PollCycleResult",
            "PollOutcome",
            "load_poll_loop_config",
            "next_poll_time",
            "request_stop_on_signals",
        ):
            assert hasattr(live_package, name)
            assert name in live_package.__all__
