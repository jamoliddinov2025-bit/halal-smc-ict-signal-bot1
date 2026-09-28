"""Phase 35F: phase-level scope, boundary, and end-to-end verification.

This file is the phase gate. It proves that Phase 35F added exactly two modules
plus exactly one read-only accessor, that every protected 35A-35E file is still
byte-identical, that no prohibited surface appeared (GAP, RECOVERY,
``last_delivery_error``, a second scheduler, persistence, an extra accessor), and
that the whole chain works end to end over a *real* poll loop and *real* durable
outbox records.

Offline only: no socket, no transport, no network.
"""

from __future__ import annotations

import ast
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import smcsignal
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.models import FailureCategory
from smcsignal.delivery.outbox.models import OutboxRecord, OutboxState
from smcsignal.live import (
    CHECKPOINT_METHODOLOGY,
    CHECKPOINT_SCHEMA_VERSION,
    OPERATOR_SNAPSHOT_SCHEMA_VERSION,
    LifecycleState,
    OperatorHealthState,
    OperatorSnapshotValidationError,
    RetryAfterAwareLivePollLoop,
    build_operator_snapshot,
    classify_health,
)
from smcsignal.live.checkpoint import CHECKPOINT_KIND
from smcsignal.live.poll_loop import LivePollLoop, LivePollLoopConfig
from smcsignal.live.retry_after import RateLimitedFeedError
from smcsignal.live.service import CycleReport
from tests.delivery.outbox.helpers import delivered, mutate, queued_record, waiting
from tests.live.test_operator import FakeClock, ScriptedRunner, buy_frame

BASELINE = "826c15d23a6a51d5dc68ad5e5e4bbd81f0d7895b"
SRC = Path(smcsignal.__file__).resolve().parent
LIVE = SRC / "live"
REPO_ROOT = SRC.parent.parent

PROTECTED = (
    "src/smcsignal/live/poll_loop.py",
    "src/smcsignal/live/market_feed.py",
    "src/smcsignal/live/config.py",
    "src/smcsignal/live/configuration_binding.py",
    "src/smcsignal/live/gap.py",
    "src/smcsignal/live/gap_aware_feed.py",
    "src/smcsignal/live/gap_aware_service.py",
    "src/smcsignal/live/retry_after.py",
)
AUTHORIZED_EXCEPTION = "src/smcsignal/live/poll_loop_retry_after.py"

GENERATED_AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


# -- git plumbing ---------------------------------------------------------------


def _baseline_available() -> bool:
    probe = subprocess.run(
        ["git", "cat-file", "-t", BASELINE],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return probe.returncode == 0 and probe.stdout.strip() == "commit"


def _baseline_bytes(path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{BASELINE}:{path}"],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout


def _changed_paths(pathspec: str) -> set[str]:
    """Tracked modifications plus untracked additions under one pathspec."""

    tracked = subprocess.run(
        ["git", "diff", "--name-only", BASELINE, "--", pathspec],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", pathspec],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout.split()
    return set(tracked) | set(untracked)


def _added_and_removed_lines(path: str) -> tuple[list[str], list[str]]:
    diff = subprocess.run(
        ["git", "diff", "--unified=0", BASELINE, "--", path],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout
    added: list[str] = []
    removed: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _imports_of(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _class_names(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


PHASE35F_MODULES = (LIVE / "operator.py", LIVE / "health.py")


# -- the exact Phase 35F footprint ----------------------------------------------


def test_phase35f_added_exactly_two_live_modules() -> None:
    expected = {
        "__init__.py",
        "checkpoint.py",
        "config.py",
        "configuration_binding.py",
        "gap.py",
        "gap_aware_feed.py",
        "gap_aware_service.py",
        "health.py",
        "market_feed.py",
        "operator.py",
        "outbox_wiring.py",
        "poll_loop.py",
        "poll_loop_retry_after.py",
        "retention.py",
        "retry_after.py",
        "runtime.py",
        "service.py",
    }
    assert {path.name for path in LIVE.glob("*.py")} == expected


def test_phase35f_added_exactly_three_live_test_modules() -> None:
    tests_live = Path(__file__).resolve().parent
    phase35f = {
        path.name
        for path in tests_live.glob("test_*.py")
        if path.name in {"test_operator.py", "test_health.py", "test_phase35f.py"}
    }
    assert phase35f == {"test_operator.py", "test_health.py", "test_phase35f.py"}
    for name in sorted(phase35f):
        assert (tests_live / name).is_file()


def test_no_other_source_file_was_touched() -> None:
    """Only the authorized package init and the authorized accessor changed."""

    if not _baseline_available():
        pytest.skip(f"baseline {BASELINE} is not present in this object store")
    changed = _changed_paths("src")
    assert changed == {
        "src/smcsignal/live/__init__.py",
        "src/smcsignal/live/health.py",
        "src/smcsignal/live/operator.py",
        AUTHORIZED_EXCEPTION,
    }


def test_only_the_authorized_guard_files_were_updated() -> None:
    if not _baseline_available():
        pytest.skip(f"baseline {BASELINE} is not present in this object store")
    changed = _changed_paths("tests")
    assert changed == {
        "tests/intelligence/test_regression.py",
        "tests/live/test_frozen_core_boundary.py",
        "tests/live/test_health.py",
        "tests/live/test_operator.py",
        "tests/live/test_phase35f.py",
        "tests/robustness/test_regression.py",
    }


# -- protected 35A-35E files ----------------------------------------------------


@pytest.mark.parametrize("path", PROTECTED)
def test_each_protected_file_is_byte_identical_to_the_baseline(path: str) -> None:
    if not _baseline_available():
        pytest.skip(f"baseline {BASELINE} is not present in this object store")
    current = (REPO_ROOT / path).read_bytes()
    assert current == _baseline_bytes(path), f"{path} was modified"


def test_the_retry_after_module_changed_only_by_the_authorized_accessor() -> None:
    if not _baseline_available():
        pytest.skip(f"baseline {BASELINE} is not present in this object store")
    added, removed = _added_and_removed_lines(AUTHORIZED_EXCEPTION)
    # Purely additive: not one existing line was removed or rewritten.
    assert removed == [], f"protected lines were changed: {removed}"
    body = [line for line in added if line.strip()]
    assert body, "expected the authorized accessor to be present"
    assert any(line.strip() == "@property" for line in body)
    assert any("def retry_after_delay(self)" in line for line in body)
    # Nothing but that one property was added: no second accessor, no scheduler.
    assert sum("def " in line for line in body) == 1
    assert not any("@property" in line for line in body[1:])


def test_the_accessor_is_the_only_property_on_the_retry_after_loop() -> None:
    properties = {
        name
        for name, value in vars(RetryAfterAwareLivePollLoop).items()
        if isinstance(value, property)
    }
    assert properties == {"retry_after_delay"}
    descriptor = RetryAfterAwareLivePollLoop.retry_after_delay
    assert isinstance(descriptor, property)
    assert descriptor.fset is None, "the accessor must not be writable"
    assert descriptor.fdel is None, "the accessor must not be deletable"


def test_the_accessor_cannot_be_written_through_an_instance() -> None:
    """A read-only property refuses assignment; the holder stays untouched."""

    clock = FakeClock()
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([1]),
        LivePollLoopConfig(timeframe="15m", jitter_min_seconds=0.0, jitter_max_seconds=0.0),
        clock=clock,
        sleep_fn=clock.advance,
        random_source=lambda: 0.0,
    )
    assert loop.retry_after_delay is None
    with pytest.raises(AttributeError):
        loop.retry_after_delay = 30.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del loop.retry_after_delay
    # The refusal is not cosmetic: nothing moved.
    assert loop.retry_after_delay is None
    assert loop._retry_after_holder == [None]
    # The descriptor is still the original property afterwards.
    assert isinstance(RetryAfterAwareLivePollLoop.retry_after_delay, property)


def test_the_accessor_exposes_the_existing_holder_and_nothing_else() -> None:
    clock = FakeClock()
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([RateLimitedFeedError(status_code=429, retry_after_seconds=17.0)]),
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
    assert loop._retry_after_holder == [17.0]
    assert loop.retry_after_delay == 17.0
    assert loop._retry_after_holder == [17.0]


def test_the_accessor_changes_neither_scheduling_nor_backoff() -> None:
    """``backoff_seconds`` keeps its exact pre-existing ``max(base, retry_after)``."""

    clock = FakeClock()
    config = LivePollLoopConfig(
        timeframe="15m",
        jitter_min_seconds=0.0,
        jitter_max_seconds=0.0,
        backoff_base_seconds=10.0,
        backoff_max_seconds=60.0,
    )
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([1]),
        config,
        clock=clock,
        sleep_fn=clock.advance,
        random_source=lambda: 0.0,
    )
    # No Retry-After in effect: the frozen base backoff is returned verbatim.
    assert loop.retry_after_delay is None
    assert loop.config.backoff_seconds(1) == 10.0
    assert loop.config.backoff_seconds(4) == 60.0
    # Reading the accessor cannot influence the schedule.
    for _ in range(5):
        assert loop.retry_after_delay is None
    assert loop.config.backoff_seconds(1) == 10.0
    assert loop.next_poll_at is None


# -- prohibited surfaces --------------------------------------------------------


@pytest.mark.parametrize("module", PHASE35F_MODULES, ids=lambda p: p.name)
def test_no_gap_recovery_or_last_delivery_error_anywhere(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        for name in names:
            lowered = name.lower()
            assert "gap" not in lowered, f"{module.name}: {name} reintroduces GAP"
            assert "recovery" not in lowered, f"{module.name}: {name} reintroduces RECOVERY"
            assert "last_delivery_error" not in lowered


def test_no_second_scheduler_was_introduced() -> None:
    for module in PHASE35F_MODULES:
        imported = _imports_of(module)
        for banned in ("threading", "asyncio", "sched", "time", "signal", "subprocess"):
            assert banned not in imported, f"{module.name} imports {banned}"
        classes = _class_names(module)
        assert not any(name.endswith("Scheduler") for name in classes)
        assert not any(name.endswith("Loop") for name in classes)
    # The frozen poll loop is still the only scheduler, and it is untouched.
    loop_subclasses = {
        name
        for path in LIVE.glob("*.py")
        for name in _class_names(path)
        if name in {"LivePollLoop", "RetryAfterAwareLivePollLoop"}
    }
    assert loop_subclasses == {"LivePollLoop", "RetryAfterAwareLivePollLoop"}
    scheduling = _imports_of(LIVE / "poll_loop.py")
    assert "signal" in scheduling and "time" in scheduling


def test_no_new_persistence_was_introduced() -> None:
    for module in PHASE35F_MODULES:
        imported = _imports_of(module)
        for banned in (
            "os",
            "pathlib",
            "json",
            "shutil",
            "tempfile",
            "pickle",
            "smcsignal.persistence",
            "smcsignal.datasets",
            "smcsignal.configurations",
        ):
            assert banned not in imported, f"{module.name} imports {banned}"
        assert not any(name.startswith("smcsignal.persistence") for name in imported), (
            f"{module.name} reaches the ledger store"
        )
        text = module.read_text(encoding="utf-8")
        assert "open(" not in text
        assert ".write(" not in text
        assert "mkdir" not in text


def test_no_delivery_transport_was_added() -> None:
    for module in PHASE35F_MODULES:
        imported = _imports_of(module)
        for banned in (
            "smcsignal.delivery.telegram",
            "smcsignal.delivery.transport",
            "smcsignal.delivery.outbox.sink",
            "smcsignal.delivery.outbox.store",
            "urllib",
            "http",
            "socket",
        ):
            assert banned not in imported, f"{module.name} imports {banned}"
        # Records are read through the frozen record model only.
        assert not any(name.startswith("smcsignal.delivery.outbox.sink") for name in imported)


def test_the_operator_never_writes_to_the_outbox() -> None:
    """Phase 35F reads durable records; it can never mutate or drain them."""

    import smcsignal.live.operator as operator

    text = Path(operator.__file__).read_text(encoding="utf-8")
    for forbidden in ("save_record", "drain(", "deliver_payload", "FileOutboxStore"):
        assert forbidden not in text, f"operator.py reaches the outbox write path: {forbidden}"


def test_no_monitoring_state_is_a_second_source_of_truth() -> None:
    for module in PHASE35F_MODULES:
        imported = _imports_of(module)
        assert not any(name.startswith("smcsignal.monitoring") for name in imported), (
            f"{module.name} reads monitoring state"
        )


def test_no_additional_accessor_was_added_to_the_live_boundary() -> None:
    """The live package gained exactly one new public property in Phase 35F."""

    if not _baseline_available():
        pytest.skip(f"baseline {BASELINE} is not present in this object store")
    offenders: list[str] = []
    for path in sorted(LIVE.glob("*.py")):
        rel = f"src/smcsignal/live/{path.name}"
        added, _removed = _added_and_removed_lines(rel)
        new_properties = [line for line in added if "@property" in line]
        if path.name == "poll_loop_retry_after.py":
            # The single authorized accessor.
            assert len(new_properties) == 1, new_properties
            continue
        if new_properties:
            offenders.append(rel)
    assert offenders == []


# -- 35A-35E boundaries ---------------------------------------------------------


def test_phase35a_boundary_poll_loop_is_the_untouched_scheduling_authority() -> None:
    poll_loop = (LIVE / "poll_loop.py").read_text(encoding="utf-8")
    imports = _imports_of(LIVE / "poll_loop.py")
    assert "35F" not in poll_loop
    assert "OperatorSnapshot" not in poll_loop
    assert "classify_health" not in poll_loop
    assert "OperatorHealthState" not in poll_loop
    assert not any(name.startswith("smcsignal.live.operator") for name in imports)
    assert not any(name.startswith("smcsignal.live.health") for name in imports)
    # The loop still exposes the read-only counters Phase 35F projects.
    for name in ("cycles", "consecutive_failures", "last_result", "history", "next_poll_at"):
        assert isinstance(getattr(LivePollLoop, name), property)


def test_phase35b_boundary_the_configuration_canon_is_reused_not_reinvented() -> None:
    operator_imports = _imports_of(LIVE / "operator.py")
    assert "smcsignal.live.configuration_binding" in operator_imports
    text = (LIVE / "operator.py").read_text(encoding="utf-8")
    assert "configuration_identity(" in text
    # No second digest/canon implementation.
    assert "hashlib" not in operator_imports
    assert "sha256" not in text


def test_phase35c_boundary_no_gap_or_retry_logic_was_reimplemented() -> None:
    for module in PHASE35F_MODULES:
        imported = _imports_of(module)
        assert "smcsignal.live.gap" not in imported
        assert "smcsignal.live.gap_aware_feed" not in imported
        assert "smcsignal.live.gap_aware_service" not in imported
        assert "smcsignal.live.retry_after" not in imported
        text = module.read_text(encoding="utf-8")
        assert "parse_retry_after" not in text
        assert "detect_continuity_gap" not in text
        assert "validate_batch_contiguity" not in text


def test_phase35d_boundary_outbox_ownership_is_unchanged() -> None:
    operator_imports = _imports_of(LIVE / "operator.py")
    assert "smcsignal.delivery.outbox.models" in operator_imports
    assert "smcsignal.delivery.outbox.store" not in operator_imports
    assert "smcsignal.delivery.outbox.sink" not in operator_imports
    assert "smcsignal.delivery.outbox.reconcile" not in operator_imports
    # The frozen record vocabulary is read, never redeclared.
    text = (LIVE / "operator.py").read_text(encoding="utf-8")
    assert "class OutboxState" not in text
    assert "class OutboxRecord" not in text
    assert "RECORD_SCHEMA" not in text


def test_phase35e_boundary_checkpoint_format_and_ownership_are_unchanged() -> None:
    assert CHECKPOINT_SCHEMA_VERSION == 1
    assert CHECKPOINT_METHODOLOGY == "live-checkpoint-v1"
    assert CHECKPOINT_KIND == "live-runtime-checkpoint"
    for module in PHASE35F_MODULES:
        imported = _imports_of(module)
        assert "smcsignal.live.checkpoint" not in imported, (
            f"{module.name} must not reach the checkpoint store"
        )
    # The tri-state is read from the frozen CycleReport, which still declares it.
    from dataclasses import fields as dc_fields

    assert "checkpoint_persisted" in {f.name for f in dc_fields(CycleReport)}


def test_analysis_and_signal_semantics_were_not_modified() -> None:
    if not _baseline_available():
        pytest.skip(f"baseline {BASELINE} is not present in this object store")
    assert _changed_paths("src/smcsignal/analysis") == set()


# -- the Phase 35F export surface -----------------------------------------------


def test_the_package_exports_the_phase35f_surface() -> None:
    import smcsignal.live as live

    required = {
        "OPERATOR_SNAPSHOT_SCHEMA_VERSION",
        "OperatorSnapshot",
        "OperatorSnapshotValidationError",
        "LifecycleState",
        "OperatorHealthState",
        "classify_health",
        "build_operator_snapshot",
    }
    assert required <= set(live.__all__)
    for name in required:
        assert hasattr(live, name)
    assert OPERATOR_SNAPSHOT_SCHEMA_VERSION == 1
    assert len(live.__all__) == len(set(live.__all__)), "duplicate export"


def test_the_health_vocabulary_is_exactly_five_states() -> None:
    assert [state.value for state in OperatorHealthState] == [
        "FAILED",
        "RECOVERING",
        "DEGRADED",
        "UNKNOWN",
        "HEALTHY",
    ]


# -- UNKNOWN versus INVALID -----------------------------------------------------


def test_unknown_is_a_state_while_invalid_is_a_rejection() -> None:
    loop = _loop([1])
    # UNKNOWN: a real, classifiable state with a valid snapshot.
    unknown = build_operator_snapshot(
        lifecycle_state=LifecycleState.STOPPED,
        configuration="configuration:test",
        loop=loop,
        generated_at=GENERATED_AT,
    )
    assert classify_health(unknown) is OperatorHealthState.UNKNOWN
    # INVALID: the snapshot cannot even be constructed, so nothing is classified.
    with pytest.raises(OperatorSnapshotValidationError):
        build_operator_snapshot(
            lifecycle_state=LifecycleState.RUNNING,
            configuration="configuration:test",
            loop=loop,
            generated_at=GENERATED_AT - timedelta(days=1),
            outbox_records=(_record("delivery:future", updated_at=GENERATED_AT),),
        )
    with pytest.raises(AnalysisInputError):
        classify_health("FAILED")  # type: ignore[arg-type]


# -- end to end -----------------------------------------------------------------


def _loop(
    outcomes: list[object], *, checkpoint_persisted: bool = True
) -> RetryAfterAwareLivePollLoop:
    clock = FakeClock()
    return _built_loop(outcomes, clock, checkpoint_persisted=checkpoint_persisted)


def _built_loop(
    outcomes: list[object],
    clock: FakeClock,
    *,
    checkpoint_persisted: bool = True,
) -> RetryAfterAwareLivePollLoop:
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner(outcomes, checkpoint_persisted=checkpoint_persisted),
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
        loop.run(max_cycles=max(1, len(outcomes)))
    except Exception:
        pass
    return loop


def _record(
    delivery_id: str,
    *,
    state: OutboxState = OutboxState.QUEUED,
    updated_at: datetime = GENERATED_AT,
    category: FailureCategory | None = None,
) -> OutboxRecord:
    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    return mutate(
        base,
        delivery_id=delivery_id,
        state=state,
        updated_at=updated_at,
        last_failure_category=category,
    )


def _classify(
    loop: LivePollLoop,
    *,
    state: LifecycleState = LifecycleState.RUNNING,
    records: tuple[object, ...] = (),
    quarantine: int = 0,
    generated_at: datetime = GENERATED_AT,
) -> OperatorHealthState:
    snapshot = build_operator_snapshot(
        lifecycle_state=state,
        configuration="configuration:test",
        loop=loop,
        generated_at=generated_at,
        outbox_records=records,  # type: ignore[arg-type]
        outbox_quarantine_count=quarantine,
    )
    return classify_health(snapshot)


def test_end_to_end_healthy_over_a_real_loop() -> None:
    loop = _loop([1, 2, 3])
    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    records = (delivered(base, attempts=1, at=GENERATED_AT),)
    assert _classify(loop, records=records) is OperatorHealthState.HEALTHY


def test_end_to_end_unknown_before_the_first_cycle() -> None:
    clock = FakeClock()
    loop = RetryAfterAwareLivePollLoop(
        ScriptedRunner([1]),
        LivePollLoopConfig(timeframe="15m", jitter_min_seconds=0.0, jitter_max_seconds=0.0),
        clock=clock,
        sleep_fn=clock.advance,
        random_source=lambda: 0.0,
    )
    assert loop.cycles == 0
    assert _classify(loop) is OperatorHealthState.UNKNOWN


def test_end_to_end_recovering_from_a_real_rate_limited_cycle() -> None:
    loop = _built_loop(
        [1, RateLimitedFeedError(status_code=429, retry_after_seconds=30.0)],
        FakeClock(),
    )
    assert loop.retry_after_delay == 30.0
    assert _classify(loop) is OperatorHealthState.RECOVERING


def test_end_to_end_degraded_from_real_outbox_backlog() -> None:
    loop = _loop([1, 2])
    pending = _record("delivery:pending", state=OutboxState.WAITING)
    assert _classify(loop, records=(pending,)) is OperatorHealthState.DEGRADED


def test_end_to_end_degraded_from_an_unpersisted_checkpoint() -> None:
    loop = _loop([1, 2], checkpoint_persisted=False)
    assert _classify(loop) is OperatorHealthState.DEGRADED


def test_end_to_end_degraded_from_a_real_failure_category() -> None:
    loop = _loop([1])
    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    failing = waiting(mutate(base, delivery_id="delivery:f"), attempts=2, at=GENERATED_AT)
    assert failing.last_failure_category is FailureCategory.TRANSPORT
    assert _classify(loop, records=(failing,)) is OperatorHealthState.DEGRADED


def test_end_to_end_degraded_from_a_quarantined_record_document() -> None:
    loop = _loop([1])
    assert _classify(loop, quarantine=1) is OperatorHealthState.DEGRADED


def test_end_to_end_failed_on_a_fatal_stop() -> None:
    loop = _loop([1])
    assert _classify(loop, state=LifecycleState.FAILED) is OperatorHealthState.FAILED


def test_end_to_end_unknown_on_a_clean_stop() -> None:
    loop = _loop([1, 2, 3])
    assert _classify(loop, state=LifecycleState.STOPPED) is OperatorHealthState.UNKNOWN


def test_end_to_end_a_later_success_clears_the_failure_category() -> None:
    loop = _loop([1])
    base = queued_record(buy_frame(), now=GENERATED_AT, max_attempts=5)
    failing = waiting(mutate(base, delivery_id="delivery:seq"), attempts=2, at=GENERATED_AT)
    recovered = delivered(failing, attempts=3, at=GENERATED_AT + timedelta(minutes=1))
    later = GENERATED_AT + timedelta(minutes=2)
    # Before: the failing record is both pending backlog and a failure category.
    assert _classify(loop, records=(failing,)) is OperatorHealthState.DEGRADED
    # After: the durable store is keyed by delivery_id, so the settled record
    # *replaces* the failing one — it is the same delivery, not a second record.
    assert recovered.delivery_id == failing.delivery_id
    assert recovered.state is OutboxState.DELIVERED
    assert recovered.last_failure_category is FailureCategory.NONE
    assert _classify(loop, records=(recovered,), generated_at=later) is OperatorHealthState.HEALTHY


def test_observing_the_loop_never_changes_it() -> None:
    loop = _loop([1, 2])
    before = (loop.cycles, loop.consecutive_failures, loop.retry_after_delay, loop.history)
    for _ in range(4):
        _classify(loop)
    after = (loop.cycles, loop.consecutive_failures, loop.retry_after_delay, loop.history)
    assert after == before
