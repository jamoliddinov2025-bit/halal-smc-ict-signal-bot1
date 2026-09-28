"""Phase 35F: the read-only operator snapshot.

Every test here drives the real ``smcsignal.live.operator`` seam. Cycle counts
come from a genuine :class:`LivePollLoop` (and, where Retry-After matters, from
``RetryAfterAwareLivePollLoop``) — never from a re-implementation — and outbox
records are genuine durable ``OutboxRecord`` values built through the existing
Phase 35D test helpers over real frozen-chain BUY frames.

Offline only: no socket, no transport, no clock read inside the module under
test (``generated_at`` is always supplied).
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import cast

import pytest

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.signal_engine import SignalStatus
from smcsignal.analysis.signal_engine.models import SignalSnapshot
from smcsignal.delivery.models import FailureCategory
from smcsignal.delivery.outbox.models import OutboxRecord, OutboxState
from smcsignal.live import (
    OPERATOR_SNAPSHOT_SCHEMA_VERSION,
    LivePollLoop,
    LivePollLoopConfig,
    OperatorSnapshot,
    OperatorSnapshotValidationError,
    RetryAfterAwareLivePollLoop,
)
from smcsignal.live.configuration_binding import (
    LiveConfigurationBinding,
    configuration_identity,
)
from smcsignal.live.health import OperatorHealthState
from smcsignal.live.operator import (
    FAILED_OUTBOX_STATES,
    OPERATOR_SNAPSHOT_METHODOLOGY,
    PENDING_OUTBOX_STATES,
    CheckpointView,
    ConfigurationView,
    CycleView,
    DeliveryView,
    LifecycleState,
    LifecycleView,
    OutboxView,
    RetryAfterView,
    build_operator_snapshot,
    checkpoint_view,
    configuration_view,
    cycle_view,
    delivery_view,
    latest_delivery_category,
    latest_outbox_record,
    outbox_view,
    retry_after_view,
)
from smcsignal.live.retry_after import RateLimitedFeedError
from smcsignal.live.service import CycleReport
from tests.backtest.helpers import configuration
from tests.delivery.outbox.helpers import delivered, mutate, queued_record, waiting

GENERATED_AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
LOOP_START = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)


# -- offline plumbing ----------------------------------------------------------


class FakeClock:
    """Injectable wall clock; advances only when told to."""

    def __init__(self, start: datetime = LOOP_START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class ScriptedRunner:
    """A ``CycleRunner`` whose outcomes are scripted, with a real report."""

    def __init__(self, outcomes: list[object], *, checkpoint_persisted: bool = True) -> None:
        self._outcomes = outcomes
        self.checkpoint_persisted = checkpoint_persisted
        self.calls = 0

    def run_cycle(self) -> CycleReport:
        self.calls += 1
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        frames = cast(int, outcome)
        return CycleReport(
            primary_candles=frames,
            higher_candles=0,
            frames=frames,
            buy_signals=0,
            delivery_states=(),
            ledger_persisted=True,
            checkpoint_persisted=self.checkpoint_persisted,
        )


def run_loop(
    outcomes: list[object],
    *,
    loop_cls: type[LivePollLoop] = LivePollLoop,
    checkpoint_persisted: bool = True,
) -> LivePollLoop:
    """Run a real poll loop through ``outcomes`` and return it."""

    clock = FakeClock()
    runner = ScriptedRunner(outcomes, checkpoint_persisted=checkpoint_persisted)
    config = LivePollLoopConfig(
        timeframe="15m",
        jitter_min_seconds=0.0,
        jitter_max_seconds=0.0,
        backoff_base_seconds=1.0,
        backoff_max_seconds=60.0,
    )
    loop = loop_cls(
        runner,
        config,
        clock=clock,
        sleep_fn=clock.advance,
        random_source=lambda: 0.0,
    )
    try:
        loop.run(max_cycles=max(1, len(outcomes)))
    except Exception:
        # A fatal outcome is re-raised by the frozen loop; the history still
        # records it, which is exactly what the snapshot projects.
        pass
    return loop


@lru_cache(maxsize=1)
def buy_frame() -> SignalSnapshot:
    """One real published BUY frame from the frozen Phase 1-23 chain."""

    from tests.outcome_tracking.helpers import signal_frames

    frames = signal_frames()  # type: ignore[no-untyped-call]
    buys = [frame for frame in frames if frame.status is SignalStatus.BUY_SIGNAL]
    assert buys, "expected at least one BUY frame from the fixture chain"
    return cast(SignalSnapshot, buys[0])


def record(
    delivery_id: str,
    *,
    state: OutboxState = OutboxState.QUEUED,
    updated_at: datetime = GENERATED_AT,
    category: FailureCategory | None = None,
) -> OutboxRecord:
    """A genuine durable record, deterministically rewritten for one case."""

    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    return mutate(
        base,
        delivery_id=delivery_id,
        state=state,
        updated_at=updated_at,
        last_failure_category=category,
    )


def snapshot(**overrides: object) -> OperatorSnapshot:
    """A valid snapshot with individual fields replaced per test."""

    base = OperatorSnapshot(
        schema_version=OPERATOR_SNAPSHOT_SCHEMA_VERSION,
        generated_at=GENERATED_AT,
        lifecycle=LifecycleView(state=LifecycleState.RUNNING),
        configuration=ConfigurationView(identity="configuration:test"),
        cycles=CycleView(completed_cycles=3, consecutive_failures=0, last_cycle_success=True),
        checkpoint=CheckpointView(last_persist_success=True),
        retry_after=RetryAfterView(active=False, delay_seconds=None),
        outbox=OutboxView(pending_count=0, failed_count=0, healthy=True),
        delivery=DeliveryView(last_failure_category=None),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# -- schema version ------------------------------------------------------------


def test_schema_version_constant_is_one() -> None:
    assert OPERATOR_SNAPSHOT_SCHEMA_VERSION == 1
    assert type(OPERATOR_SNAPSHOT_SCHEMA_VERSION) is int


def test_built_snapshot_carries_schema_version_one() -> None:
    loop = run_loop([1])
    built = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
    )
    assert built.schema_version == 1
    assert built.methodology == OPERATOR_SNAPSHOT_METHODOLOGY


@pytest.mark.parametrize("bad", [0, 2, -1, True, "1", 1.0])
def test_any_other_schema_version_is_rejected(bad: object) -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="schema_version"):
        snapshot(schema_version=bad)


def test_wrong_methodology_is_rejected() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="methodology"):
        snapshot(methodology="live-operator-snapshot-v2")


# -- every snapshot field ------------------------------------------------------


def test_snapshot_exposes_exactly_the_required_fields() -> None:
    names = tuple(field.name for field in fields(OperatorSnapshot))
    assert names == (
        "schema_version",
        "generated_at",
        "lifecycle",
        "configuration",
        "cycles",
        "checkpoint",
        "retry_after",
        "outbox",
        "delivery",
        "methodology",
    )


def test_every_view_exposes_its_fields() -> None:
    assert tuple(f.name for f in fields(LifecycleView)) == ("state",)
    assert tuple(f.name for f in fields(ConfigurationView)) == ("identity",)
    assert tuple(f.name for f in fields(CycleView)) == (
        "completed_cycles",
        "consecutive_failures",
        "last_cycle_success",
    )
    assert tuple(f.name for f in fields(CheckpointView)) == ("last_persist_success",)
    assert tuple(f.name for f in fields(RetryAfterView)) == ("active", "delay_seconds")
    assert tuple(f.name for f in fields(OutboxView)) == (
        "pending_count",
        "failed_count",
        "healthy",
    )
    assert tuple(f.name for f in fields(DeliveryView)) == ("last_failure_category",)


def test_snapshot_is_immutable() -> None:
    built = snapshot()
    with pytest.raises(AttributeError):
        built.cycles = CycleView(0, 0, None)  # type: ignore[misc]
    with pytest.raises(AttributeError):
        built.generated_at = GENERATED_AT  # type: ignore[misc]


def test_lifecycle_vocabulary_has_exactly_three_members() -> None:
    assert tuple(state.value for state in LifecycleState) == ("RUNNING", "STOPPED", "FAILED")
    assert not hasattr(LifecycleState, "GAP")
    assert not hasattr(LifecycleState, "RECOVERY")


def test_lifecycle_state_must_be_a_lifecycle_state() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="lifecycle"):
        snapshot(lifecycle=LifecycleView(state="RUNNING"))  # type: ignore[arg-type]
    with pytest.raises(OperatorSnapshotValidationError, match="lifecycle"):
        snapshot(lifecycle=LifecycleState.RUNNING)


# -- nullability ---------------------------------------------------------------


def test_last_cycle_success_is_nullable_only_before_the_first_cycle() -> None:
    assert snapshot(cycles=CycleView(0, 0, None)).cycles.last_cycle_success is None
    with pytest.raises(OperatorSnapshotValidationError, match="before the first completed cycle"):
        snapshot(cycles=CycleView(0, 0, True))
    with pytest.raises(OperatorSnapshotValidationError, match="once at least one cycle completed"):
        snapshot(cycles=CycleView(2, 0, None))


def test_checkpoint_view_is_a_genuine_tri_state() -> None:
    for value in (True, False, None):
        assert snapshot(checkpoint=CheckpointView(value)).checkpoint.last_persist_success is value
    with pytest.raises(OperatorSnapshotValidationError, match="last_persist_success"):
        snapshot(checkpoint=CheckpointView("yes"))  # type: ignore[arg-type]


def test_checkpoint_tri_state_distinguishes_not_attempted_from_failed() -> None:
    """``None`` (nothing attempted) is not ``False`` (attempted, not persisted)."""

    not_attempted = CheckpointView(last_persist_success=None)
    attempted_failed = CheckpointView(last_persist_success=False)
    attempted_ok = CheckpointView(last_persist_success=True)
    assert not_attempted != attempted_failed
    assert attempted_failed != attempted_ok
    assert len({not_attempted, attempted_failed, attempted_ok}) == 3


def test_delivery_category_is_nullable_and_none_means_no_failure() -> None:
    assert snapshot().delivery.last_failure_category is None
    assert (
        snapshot(delivery=DeliveryView(FailureCategory.TRANSPORT)).delivery.last_failure_category
        is FailureCategory.TRANSPORT
    )


def test_retry_after_delay_is_nullable_and_paired_with_active() -> None:
    assert snapshot().retry_after.delay_seconds is None
    assert snapshot(retry_after=RetryAfterView(True, 30.0)).retry_after.delay_seconds == 30.0
    with pytest.raises(OperatorSnapshotValidationError, match="delay_seconds"):
        snapshot(retry_after=RetryAfterView(active=True, delay_seconds=None))
    with pytest.raises(OperatorSnapshotValidationError, match="retry_after.active"):
        snapshot(retry_after=RetryAfterView(active=False, delay_seconds=30.0))


# -- retained invariants -------------------------------------------------------


def test_consecutive_failures_cannot_exceed_completed_cycles() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="cannot exceed"):
        snapshot(cycles=CycleView(2, 3, False))


def test_positive_consecutive_failures_requires_a_failed_last_cycle() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="must be False while"):
        snapshot(cycles=CycleView(4, 2, True))
    assert snapshot(cycles=CycleView(4, 2, False)).cycles.consecutive_failures == 2


def test_a_successful_last_cycle_requires_zero_consecutive_failures() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="must be zero when"):
        snapshot(cycles=CycleView(4, 1, True))


def test_a_fatal_first_failure_is_a_valid_zero_counter_state() -> None:
    """A non-recoverable failure does not increment the loop's counter."""

    built = snapshot(cycles=CycleView(1, 0, False))
    assert built.cycles.consecutive_failures == 0
    assert built.cycles.last_cycle_success is False


def test_failure_category_none_must_be_represented_as_null() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="FailureCategory.NONE"):
        snapshot(delivery=DeliveryView(FailureCategory.NONE))


def test_failure_category_must_be_a_failure_category_or_null() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="FailureCategory or None"):
        snapshot(delivery=DeliveryView("transport"))  # type: ignore[arg-type]


def test_retry_after_delay_must_be_a_finite_nonnegative_number() -> None:
    for bad in (-1.0, float("nan"), float("inf"), "30", True):
        with pytest.raises(OperatorSnapshotValidationError, match="delay_seconds"):
            snapshot(retry_after=RetryAfterView(active=True, delay_seconds=bad))  # type: ignore[arg-type]
    assert snapshot(retry_after=RetryAfterView(True, 0.0)).retry_after.delay_seconds == 0.0


def test_outbox_counts_must_be_nonnegative_integers() -> None:
    for bad in (-1, "0", 1.5, True):
        with pytest.raises(OperatorSnapshotValidationError, match="pending_count"):
            snapshot(outbox=OutboxView(pending_count=bad, failed_count=0, healthy=True))  # type: ignore[arg-type]
        with pytest.raises(OperatorSnapshotValidationError, match="failed_count"):
            snapshot(outbox=OutboxView(pending_count=0, failed_count=bad, healthy=True))  # type: ignore[arg-type]


def test_outbox_healthy_must_be_a_strict_boolean() -> None:
    for bad in ("true", 1, 0, None):
        with pytest.raises(OperatorSnapshotValidationError, match="outbox.healthy"):
            snapshot(outbox=OutboxView(pending_count=0, failed_count=0, healthy=bad))  # type: ignore[arg-type]


def test_cycle_counts_must_be_nonnegative_integers() -> None:
    for bad in (-1, "3", 3.0, True):
        with pytest.raises(OperatorSnapshotValidationError, match="completed_cycles"):
            snapshot(cycles=CycleView(bad, 0, None))  # type: ignore[arg-type]


def test_configuration_identity_must_be_a_nonempty_trimmed_string() -> None:
    for bad in ("", "   ", " configuration:x ", 7, None):
        with pytest.raises(OperatorSnapshotValidationError, match="configuration.identity"):
            snapshot(configuration=ConfigurationView(bad))  # type: ignore[arg-type]


def test_generated_at_must_be_timezone_aware() -> None:
    with pytest.raises(OperatorSnapshotValidationError, match="generated_at"):
        snapshot(generated_at=datetime(2026, 9, 28, 12, 0))
    with pytest.raises(OperatorSnapshotValidationError, match="generated_at"):
        snapshot(generated_at="2026-09-28T12:00:00+00:00")


# -- deterministic validation errors -------------------------------------------


def test_validation_rejects_and_reports_every_violation_in_field_order() -> None:
    with pytest.raises(OperatorSnapshotValidationError) as excinfo:
        OperatorSnapshot(
            schema_version=9,
            generated_at=datetime(2026, 9, 28, 12, 0),
            lifecycle=LifecycleView(state="RUNNING"),  # type: ignore[arg-type]
            configuration=ConfigurationView(""),
            cycles=CycleView(-1, 5, "maybe"),  # type: ignore[arg-type]
            checkpoint=CheckpointView("yes"),  # type: ignore[arg-type]
            retry_after=RetryAfterView("yes", "30"),  # type: ignore[arg-type]
            outbox=OutboxView(-1, -2, "yes"),  # type: ignore[arg-type]
            delivery=DeliveryView(FailureCategory.NONE),
        )
    message = str(excinfo.value)
    order = [
        "schema_version",
        "generated_at",
        "lifecycle.state",
        "configuration.identity",
        "cycles.completed_cycles",
        "checkpoint.last_persist_success",
        "retry_after.active",
        "outbox.pending_count",
        "outbox.failed_count",
        "outbox.healthy",
        "delivery.last_failure_category",
    ]
    positions = [message.index(field) for field in order]
    assert positions == sorted(positions), "violations must be reported in field order"
    assert message.startswith("invalid operator snapshot (")


def test_validation_errors_are_deterministic_for_identical_input() -> None:
    def build() -> str:
        try:
            snapshot(cycles=CycleView(1, 4, True))
        except OperatorSnapshotValidationError as exc:
            return str(exc)
        raise AssertionError("expected a rejection")

    expected = (
        "invalid operator snapshot (cycles.consecutive_failures: "
        "cannot exceed cycles.completed_cycles)"
    )
    assert build() == build() == expected


def test_validation_never_mutates_the_offending_value() -> None:
    cycles = CycleView(1, 4, True)
    with pytest.raises(OperatorSnapshotValidationError):
        snapshot(cycles=cycles)
    assert cycles == CycleView(1, 4, True)


# -- generated_at and timestamp ordering ---------------------------------------


def test_generated_at_is_reported_verbatim() -> None:
    loop = run_loop([1])
    built = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
    )
    assert built.generated_at == GENERATED_AT
    assert built.generated_at.utcoffset() is not None


def test_a_naive_generated_at_is_rejected_by_the_builder() -> None:
    loop = run_loop([1])
    with pytest.raises(OperatorSnapshotValidationError, match="generated_at"):
        build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=datetime(2026, 9, 28, 12, 0),
        )


def test_generated_at_may_not_predate_the_evidence_it_summarizes() -> None:
    loop = run_loop([1])
    late = GENERATED_AT + timedelta(minutes=5)
    records = (record("delivery:late", updated_at=late),)
    with pytest.raises(OperatorSnapshotValidationError, match="predate"):
        build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=GENERATED_AT,
            outbox_records=records,
        )


def test_generated_at_equal_to_the_latest_evidence_is_accepted() -> None:
    loop = run_loop([1])
    records = (record("delivery:exact", updated_at=GENERATED_AT),)
    built = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
        outbox_records=records,
    )
    assert built.generated_at == GENERATED_AT


def test_successive_snapshots_are_ordered_by_a_monotonic_clock() -> None:
    clock = FakeClock(GENERATED_AT)
    loop = run_loop([1])
    stamps = []
    for _ in range(5):
        built = build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=clock(),
        )
        stamps.append(built.generated_at)
        clock.advance(60.0)
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == 5


# -- configuration identity consistency ----------------------------------------


def test_configuration_identity_matches_the_frozen_phase35b_canon() -> None:
    pipeline = BacktestConfiguration()
    view = configuration_view(pipeline)
    assert view.identity == configuration_identity(pipeline)
    assert view.identity.startswith("configuration:")


def test_configuration_identity_is_consistent_across_snapshots() -> None:
    pipeline = BacktestConfiguration()
    loop = run_loop([1])
    first = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration=pipeline,
        loop=loop,
        generated_at=GENERATED_AT,
    )
    second = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration=pipeline,
        loop=loop,
        generated_at=GENERATED_AT + timedelta(seconds=1),
    )
    assert first.configuration.identity == second.configuration.identity


def test_a_binding_identity_is_accepted_verbatim() -> None:
    pipeline = BacktestConfiguration()
    binding = LiveConfigurationBinding(
        configuration_key="configuration:live:BTCUSDT:15m",
        identity=configuration_identity(pipeline),
        outcome="declared",
    )
    assert configuration_view(binding).identity == configuration_identity(pipeline)


def test_an_identity_string_is_accepted_verbatim() -> None:
    assert configuration_view("configuration:abc").identity == "configuration:abc"


def test_configuration_view_rejects_an_unsupported_source() -> None:
    with pytest.raises(AnalysisInputError):
        configuration_view(7)  # type: ignore[arg-type]


def test_a_different_configuration_produces_a_different_identity() -> None:
    loop = run_loop([1])
    first = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration=configuration(threshold=10),
        loop=loop,
        generated_at=GENERATED_AT,
    )
    second = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration=configuration(threshold=40),
        loop=loop,
        generated_at=GENERATED_AT,
    )
    assert first.configuration.identity == configuration_identity(configuration(threshold=10))
    assert second.configuration.identity == configuration_identity(configuration(threshold=40))
    assert first.configuration.identity != second.configuration.identity


# -- loop-sourced cycle counts -------------------------------------------------


def test_pre_first_cycle_reports_nothing_observed() -> None:
    loop = LivePollLoop(
        ScriptedRunner([1]),
        LivePollLoopConfig(timeframe="15m", jitter_min_seconds=0.0, jitter_max_seconds=0.0),
        clock=FakeClock(),
        sleep_fn=lambda seconds: None,
        random_source=lambda: 0.0,
    )
    view = cycle_view(loop)
    assert view.completed_cycles == 0
    assert view.consecutive_failures == 0
    assert view.last_cycle_success is None
    assert checkpoint_view(loop).last_persist_success is None


def test_cycle_counts_are_read_from_the_real_loop() -> None:
    loop = run_loop([2, 3, 4])
    view = cycle_view(loop)
    assert view.completed_cycles == loop.cycles == 3
    assert view.consecutive_failures == loop.consecutive_failures == 0
    assert view.last_cycle_success is True


def test_a_failing_last_cycle_is_reported_as_false() -> None:
    loop = run_loop([1, RateLimitedFeedError(status_code=429, retry_after_seconds=5.0)])
    view = cycle_view(loop)
    assert view.completed_cycles == 2
    assert view.consecutive_failures == 1
    assert view.last_cycle_success is False


def test_the_failure_counter_resets_after_a_recovery() -> None:
    loop = run_loop(
        [
            1,
            RateLimitedFeedError(status_code=429, retry_after_seconds=1.0),
            2,
        ]
    )
    view = cycle_view(loop)
    assert view.completed_cycles == 3
    assert view.consecutive_failures == 0
    assert view.last_cycle_success is True


def test_a_fatal_failure_is_recorded_without_a_counter_increment() -> None:
    from smcsignal.live.config import LiveConfigurationError

    loop = run_loop([LiveConfigurationError("fatal")])
    view = cycle_view(loop)
    assert view.completed_cycles == 1
    assert view.consecutive_failures == 0
    assert view.last_cycle_success is False


def test_cycle_view_requires_a_real_loop() -> None:
    with pytest.raises(AnalysisInputError):
        cycle_view(object())  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        checkpoint_view(object())  # type: ignore[arg-type]


def test_the_loop_is_the_only_counter_and_is_never_mutated() -> None:
    loop = run_loop([1, 2])
    before = (loop.cycles, loop.consecutive_failures, loop.history)
    for _ in range(3):
        cycle_view(loop)
        checkpoint_view(loop)
    assert (loop.cycles, loop.consecutive_failures, loop.history) == before


# -- checkpoint tri-state semantics --------------------------------------------


def test_checkpoint_view_projects_the_real_report_flag() -> None:
    persisted = run_loop([1], checkpoint_persisted=True)
    assert checkpoint_view(persisted).last_persist_success is True
    absent = run_loop([1], checkpoint_persisted=False)
    assert checkpoint_view(absent).last_persist_success is False


def test_the_builder_reads_the_checkpoint_tri_state_from_the_loop() -> None:
    loop = run_loop([1], checkpoint_persisted=False)
    built = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
    )
    assert built.checkpoint.last_persist_success is False


def test_an_explicit_checkpoint_tri_state_requires_opting_out_of_the_loop() -> None:
    loop = run_loop([1])
    built = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
        checkpoint_from_loop=False,
        checkpoint_last_persist_success=None,
    )
    assert built.checkpoint.last_persist_success is None
    with pytest.raises(AnalysisInputError):
        build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=GENERATED_AT,
            checkpoint_last_persist_success=True,
        )
    with pytest.raises(AnalysisInputError):
        build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=GENERATED_AT,
            checkpoint_from_loop=False,
            checkpoint_last_persist_success="yes",  # type: ignore[arg-type]
        )


# -- Retry-After semantics -----------------------------------------------------


def test_retry_after_is_inactive_before_any_failure() -> None:
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([1]),
        LivePollLoopConfig(timeframe="15m", jitter_min_seconds=0.0, jitter_max_seconds=0.0),
        clock=FakeClock(),
        sleep_fn=lambda seconds: None,
        random_source=lambda: 0.0,
    )
    assert loop.retry_after_delay is None
    view = retry_after_view(loop)
    assert view.active is False
    assert view.delay_seconds is None


def test_retry_after_becomes_active_from_a_real_rate_limited_cycle() -> None:
    clock = FakeClock()
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([RateLimitedFeedError(status_code=429, retry_after_seconds=30.0)]),
        LivePollLoopConfig(
            timeframe="15m",
            jitter_min_seconds=0.0,
            jitter_max_seconds=0.0,
            backoff_base_seconds=1.0,
            backoff_max_seconds=60.0,
        ),
        clock=clock,
        sleep_fn=clock.advance,
        random_source=lambda: 0.0,
    )
    try:
        loop.run(max_cycles=1)
    except Exception:
        pass
    assert loop.retry_after_delay == 30.0
    view = retry_after_view(loop)
    assert view.active is True
    assert view.delay_seconds == 30.0


def test_a_successful_cycle_clears_retry_after() -> None:
    clock = FakeClock()
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([RateLimitedFeedError(status_code=429, retry_after_seconds=30.0), 1]),
        LivePollLoopConfig(
            timeframe="15m",
            jitter_min_seconds=0.0,
            jitter_max_seconds=0.0,
            backoff_base_seconds=1.0,
            backoff_max_seconds=60.0,
        ),
        clock=clock,
        sleep_fn=clock.advance,
        random_source=lambda: 0.0,
    )
    loop.run(max_cycles=2)
    assert loop.retry_after_delay is None
    assert retry_after_view(loop).active is False


def test_a_loop_without_retry_after_support_reports_inactive() -> None:
    loop = run_loop([1])
    assert not hasattr(loop, "retry_after_delay")
    assert retry_after_view(loop).active is False


def test_reading_retry_after_never_mutates_the_holder_or_the_loop() -> None:
    clock = FakeClock()
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([RateLimitedFeedError(status_code=429, retry_after_seconds=45.0)]),
        LivePollLoopConfig(
            timeframe="15m",
            jitter_min_seconds=0.0,
            jitter_max_seconds=0.0,
            backoff_base_seconds=1.0,
            backoff_max_seconds=60.0,
        ),
        clock=clock,
        sleep_fn=clock.advance,
        random_source=lambda: 0.0,
    )
    try:
        loop.run(max_cycles=1)
    except Exception:
        pass
    before = (loop.retry_after_delay, loop.next_poll_at, loop.consecutive_failures, loop.cycles)
    for _ in range(5):
        assert loop.retry_after_delay == 45.0
        retry_after_view(loop)
    assert (loop.retry_after_delay, loop.next_poll_at, loop.consecutive_failures, loop.cycles) == (
        before
    )


def test_retry_after_delay_is_the_only_authorized_accessor() -> None:
    """Exactly one public read-only accessor was added; it exposes holder[0]."""

    from smcsignal.live import poll_loop_retry_after as module

    added = [
        name
        for name, value in vars(module.RetryAfterAwareLivePollLoop).items()
        if isinstance(value, property) and name == "retry_after_delay"
    ]
    assert added == ["retry_after_delay"]
    properties = {
        name
        for name, value in vars(module.RetryAfterAwareLivePollLoop).items()
        if isinstance(value, property)
    }
    assert properties == {"retry_after_delay"}
    descriptor = cast(property, module.RetryAfterAwareLivePollLoop.retry_after_delay)
    assert descriptor.fset is None


def test_retry_after_view_rejects_an_invalid_delay() -> None:
    class Broken:
        retry_after_delay = float("nan")

    with pytest.raises(AnalysisInputError):
        retry_after_view(Broken())


# -- outbox aggregation --------------------------------------------------------


def test_an_empty_outbox_aggregates_to_zero_and_healthy() -> None:
    view = outbox_view(())
    assert view.pending_count == 0
    assert view.failed_count == 0
    assert view.healthy is True


def test_pending_states_are_counted_as_pending() -> None:
    records = tuple(
        record(f"delivery:p{index}", state=state, category=None)
        for index, state in enumerate(sorted(PENDING_OUTBOX_STATES, key=str))
    )
    assert {state for state in PENDING_OUTBOX_STATES} == {
        OutboxState.QUEUED,
        OutboxState.IN_FLIGHT,
        OutboxState.WAITING,
    }
    view = outbox_view(records)
    assert view.pending_count == len(PENDING_OUTBOX_STATES)
    assert view.failed_count == 0


def test_terminal_non_delivered_states_are_counted_as_failed() -> None:
    assert {state for state in FAILED_OUTBOX_STATES} == {
        OutboxState.FAILED_TERMINAL,
        OutboxState.EXHAUSTED,
        OutboxState.AMBIGUOUS,
    }
    assert OutboxState.DELIVERED not in FAILED_OUTBOX_STATES
    records = tuple(
        record(f"delivery:f{index}", state=state)
        for index, state in enumerate(sorted(FAILED_OUTBOX_STATES, key=str))
    )
    view = outbox_view(records)
    assert view.failed_count == len(FAILED_OUTBOX_STATES)
    assert view.pending_count == 0


def test_a_delivered_record_is_neither_pending_nor_failed() -> None:
    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    settled = delivered(base, attempts=1, at=GENERATED_AT)
    view = outbox_view((settled,))
    assert view.pending_count == 0
    assert view.failed_count == 0
    assert view.healthy is True


def test_pending_and_failed_counts_are_disjoint_over_one_record_set() -> None:
    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    records = (
        mutate(base, delivery_id="delivery:q1", state=OutboxState.QUEUED),
        mutate(base, delivery_id="delivery:q2", state=OutboxState.WAITING, attempts_used=1),
        mutate(base, delivery_id="delivery:f1", state=OutboxState.EXHAUSTED, attempts_used=5),
        delivered(mutate(base, delivery_id="delivery:d1"), attempts=1, at=GENERATED_AT),
    )
    view = outbox_view(records)
    assert view.pending_count == 2
    assert view.failed_count == 1
    assert view.pending_count + view.failed_count == 3 < len(records)


def test_store_integrity_is_a_separate_axis_from_backlog() -> None:
    assert outbox_view((), quarantine_count=1).healthy is False
    assert outbox_view((), meta_failure="meta bootstrap failed").healthy is False
    assert outbox_view((), quarantine_count=0, meta_failure=None).healthy is True
    # Backlog alone does not make the store unhealthy.
    backlog = outbox_view((record("delivery:q", state=OutboxState.QUEUED),))
    assert backlog.pending_count == 1
    assert backlog.healthy is True


def test_outbox_view_rejects_malformed_input() -> None:
    with pytest.raises(AnalysisInputError):
        outbox_view(("not-a-record",))  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        outbox_view((), quarantine_count=-1)
    with pytest.raises(AnalysisInputError):
        outbox_view((), meta_failure=7)  # type: ignore[arg-type]


# -- latest delivery-category selection ----------------------------------------


def test_an_empty_record_set_has_no_delivery_category() -> None:
    assert latest_delivery_category(()) is None
    assert latest_outbox_record(()) is None
    assert delivery_view(()).last_failure_category is None


def test_the_latest_record_wins_by_updated_at() -> None:
    earlier = record(
        "delivery:earlier",
        state=OutboxState.EXHAUSTED,
        updated_at=GENERATED_AT - timedelta(minutes=10),
        category=FailureCategory.NETWORK,
    )
    later = record(
        "delivery:later",
        state=OutboxState.WAITING,
        updated_at=GENERATED_AT,
        category=FailureCategory.RATE_LIMIT,
    )
    assert latest_outbox_record((earlier, later)) is later
    assert latest_outbox_record((later, earlier)) is later
    assert latest_delivery_category((earlier, later)) is FailureCategory.RATE_LIMIT


def test_delivery_id_is_the_deterministic_tie_break() -> None:
    first = record("delivery:aaa", state=OutboxState.WAITING, category=FailureCategory.NETWORK)
    second = record("delivery:zzz", state=OutboxState.WAITING, category=FailureCategory.CONFIG)
    assert first.updated_at == second.updated_at
    assert latest_outbox_record((first, second)) is second
    assert latest_outbox_record((second, first)) is second
    assert latest_delivery_category((first, second)) is FailureCategory.CONFIG


def test_every_non_none_category_maps_through_verbatim() -> None:
    for category in FailureCategory:
        if category is FailureCategory.NONE:
            continue
        rec = record("delivery:c", state=OutboxState.WAITING, category=category)
        assert latest_delivery_category((rec,)) is category


def test_category_none_maps_to_null() -> None:
    rec = record("delivery:none", state=OutboxState.QUEUED, category=FailureCategory.NONE)
    assert latest_delivery_category((rec,)) is None


def test_a_null_category_maps_to_null() -> None:
    rec = record("delivery:null", state=OutboxState.QUEUED, category=None)
    assert latest_delivery_category((rec,)) is None


def test_a_later_success_clears_a_previous_failure_category() -> None:
    """The category is the latest record's, never sticky history."""

    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    failing = waiting(mutate(base, delivery_id="delivery:seq"), attempts=2, at=GENERATED_AT)
    assert failing.last_failure_category is FailureCategory.TRANSPORT
    assert latest_delivery_category((failing,)) is FailureCategory.TRANSPORT

    recovered = delivered(failing, attempts=3, at=GENERATED_AT + timedelta(minutes=1))
    assert recovered.state is OutboxState.DELIVERED
    assert recovered.last_failure_category is FailureCategory.NONE
    assert latest_delivery_category((recovered,)) is None
    assert latest_delivery_category((failing, recovered)) is None


def test_a_later_success_on_a_different_record_clears_the_category() -> None:
    failing = record(
        "delivery:failing",
        state=OutboxState.EXHAUSTED,
        updated_at=GENERATED_AT - timedelta(minutes=2),
        category=FailureCategory.TIMEOUT,
    )
    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    succeeded = delivered(
        mutate(base, delivery_id="delivery:succeeded"), attempts=1, at=GENERATED_AT
    )
    assert latest_delivery_category((failing, succeeded)) is None


def test_the_snapshot_delivery_view_matches_the_direct_selection() -> None:
    loop = run_loop([1])
    failing = record("delivery:failing", state=OutboxState.WAITING, category=FailureCategory.BUILD)
    built = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
        outbox_records=(failing,),
    )
    assert built.delivery.last_failure_category is FailureCategory.BUILD
    assert built.outbox.pending_count == 1
    assert built.outbox.failed_count == 0


def test_latest_outbox_record_rejects_malformed_input() -> None:
    with pytest.raises(AnalysisInputError):
        latest_outbox_record((7,))  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        outbox_view("delivery:abc")  # type: ignore[arg-type]


# -- the composition entry point -----------------------------------------------


def test_the_builder_composes_every_view_from_the_frozen_seams() -> None:
    loop = run_loop([1, RateLimitedFeedError(status_code=429, retry_after_seconds=12.0)])
    retry_clock = FakeClock()
    retry_loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([1, RateLimitedFeedError(status_code=429, retry_after_seconds=12.0)]),
        LivePollLoopConfig(
            timeframe="15m",
            jitter_min_seconds=0.0,
            jitter_max_seconds=0.0,
            backoff_base_seconds=1.0,
            backoff_max_seconds=60.0,
        ),
        clock=retry_clock,
        sleep_fn=retry_clock.advance,
        random_source=lambda: 0.0,
    )
    try:
        retry_loop.run(max_cycles=2)
    except Exception:
        pass
    records = (record("delivery:x", state=OutboxState.QUEUED),)
    built = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration=BacktestConfiguration(),
        loop=retry_loop,
        generated_at=GENERATED_AT,
        outbox_records=records,
        outbox_quarantine_count=1,
    )
    assert built.lifecycle.state is LifecycleState.RUNNING
    assert built.configuration.identity == configuration_identity(BacktestConfiguration())
    assert built.cycles.completed_cycles == 2
    assert built.cycles.last_cycle_success is False
    assert built.retry_after.active is True
    assert built.retry_after.delay_seconds == 12.0
    assert built.outbox.pending_count == 1
    assert built.outbox.healthy is False
    assert built.delivery.last_failure_category is None
    assert built.checkpoint.last_persist_success is True
    assert built == build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration=BacktestConfiguration(),
        loop=retry_loop,
        generated_at=GENERATED_AT,
        outbox_records=records,
        outbox_quarantine_count=1,
    )
    # The plain loop still composes; it simply reports no Retry-After.
    assert cycle_view(loop).completed_cycles == 2


def test_the_builder_requires_a_lifecycle_state() -> None:
    loop = run_loop([1])
    with pytest.raises(AnalysisInputError):
        build_operator_snapshot(
            lifecycle_state="RUNNING",  # type: ignore[arg-type]
            configuration="configuration:test",
            loop=loop,
            generated_at=GENERATED_AT,
        )
    with pytest.raises(AnalysisInputError):
        build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=GENERATED_AT,
            checkpoint_from_loop="yes",  # type: ignore[arg-type]
        )


def test_the_builder_reads_no_clock_of_its_own() -> None:
    """``generated_at`` is always supplied; nothing is read from the wall clock."""

    loop = run_loop([1])
    # The same supplied instant, observed at two different real moments, must
    # produce the identical stamp — a read of the wall clock could not.
    first = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
    )
    deadline = datetime.now(UTC) + timedelta(milliseconds=250)
    while datetime.now(UTC) < deadline:
        pass
    second = build_operator_snapshot(
        lifecycle_state=LifecycleState.RUNNING,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
    )
    assert first.generated_at == GENERATED_AT
    assert second.generated_at == GENERATED_AT
    assert first.generated_at == second.generated_at
    # A supplied past instant is reported verbatim too, never "corrected" to now.
    past = datetime(2000, 1, 1, tzinfo=UTC)
    assert (
        build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=past,
        ).generated_at
        == past
    )


# -- prohibited-surface guards -------------------------------------------------


def test_no_gap_recovery_or_last_delivery_error_identifier_exists() -> None:
    """No GAP/RECOVERY member and no ``last_delivery_error`` identifier anywhere."""

    import smcsignal.live.health as health
    import smcsignal.live.operator as operator

    for module in (operator, health):
        for name in dir(module):
            lowered = name.lower()
            assert "gap" not in lowered, f"{module.__name__}.{name} reintroduces GAP"
            assert "recovery" not in lowered, f"{module.__name__}.{name} reintroduces RECOVERY"
            assert "last_delivery_error" not in lowered
    assert not hasattr(LifecycleState, "GAP")
    assert not hasattr(LifecycleState, "RECOVERY")
    assert not hasattr(OperatorHealthState, "GAP")
    assert not hasattr(OperatorHealthState, "RECOVERY")
    for field_name in {f.name for f in fields(OperatorSnapshot)} | {
        f.name
        for view in (
            LifecycleView,
            CycleView,
            CheckpointView,
            RetryAfterView,
            OutboxView,
            DeliveryView,
            ConfigurationView,
        )
        for f in fields(view)
    }:
        assert "last_delivery_error" not in field_name
        assert "gap" not in field_name


def test_the_operator_module_performs_no_io_and_owns_no_state() -> None:
    import ast
    from pathlib import Path

    import smcsignal.live.operator as operator

    tree = ast.parse(Path(operator.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in ("socket", "urllib", "http", "threading", "subprocess", "pathlib", "os"):
        assert banned not in imported, f"operator.py imports {banned}"
    # No module-level mutable state: every module-level binding is a constant,
    # a class, a function, or a frozen set.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assert isinstance(target, ast.Name), ast.dump(target)
                assert target.id.isupper() or target.id == "__all__", (
                    f"operator.py holds mutable module state: {target.id}"
                )


def test_a_snapshot_records_no_delivery_transport_or_persistence_handle() -> None:
    built = snapshot()
    for value in (
        built.lifecycle,
        built.configuration,
        built.cycles,
        built.checkpoint,
        built.retry_after,
        built.outbox,
        built.delivery,
    ):
        for field in fields(value):
            held = getattr(value, field.name)
            assert held is None or isinstance(held, (bool, int, float, str, FailureCategory))
