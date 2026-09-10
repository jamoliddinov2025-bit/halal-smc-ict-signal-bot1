"""Phase 25B-3 tests: monitoring is a leaf, and no failure escapes it."""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from smcsignal.monitoring import (
    CounterMetric,
    FixedClock,
    HealthCode,
    HealthEvent,
    MonitoredComponent,
    MonitoringConfig,
    MonitoringSession,
    NullMonitor,
    build_health_event,
)

MONITORING = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "monitoring"
START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LATER = START + timedelta(minutes=1)

# Everything added from Phase 25B-3 onwards. The 25B-1/25B-2 modules are frozen
# and are screened by test_clock.py and test_scope.py; config.py legitimately
# reads a file, and the observers legitimately read upstream value types (their
# exact boundary is pinned by test_scope.py).
NEW_MODULES = (
    "monitor.py",
    "report.py",
    "session.py",
    "serialization.py",
    "data.py",
)
UPSTREAM_READERS = ("serialization.py", "data.py")
FORBIDDEN_MODULES = ("threading", "asyncio", "concurrent", "socket", "urllib", "http", "logging")
FORBIDDEN_CALLS = ("open", "print", "input", "exec", "eval", "compile", "__import__")


def _trees() -> list[tuple[str, ast.Module]]:
    return [
        (path.name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(MONITORING.rglob("*.py"))
        if path.name in NEW_MODULES
    ]


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_no_new_module_imports_a_concurrency_or_io_framework() -> None:
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in FORBIDDEN_MODULES, f"{name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in FORBIDDEN_MODULES, f"{name} imports from {node.module}"


def test_no_new_module_calls_an_ambient_capability() -> None:
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                dotted = _dotted(node.func)
                assert dotted.split(".")[-1] not in FORBIDDEN_CALLS, f"{name} calls {dotted}"


def test_the_pure_core_touches_only_monitoring_and_stdlib() -> None:
    for name, tree in _trees():
        if name in UPSTREAM_READERS:
            continue  # each reads exactly one pinned upstream surface
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in sys.stdlib_module_names, (
                        f"{name}: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "__future__" or module.split(".")[0] in sys.stdlib_module_names:
                    continue  # standard library is always allowed
                assert module.startswith("smcsignal.monitoring"), f"{name}: {module}"


def test_no_new_module_reaches_a_producer_or_a_decision() -> None:
    banned = (
        "smcsignal.analysis.signal_engine",
        "smcsignal.analysis.improvement",
        "smcsignal.analysis.halal_filter",
        "smcsignal.analysis.signal_eligibility",
        "smcsignal.delivery",
    )
    assert set(NEW_MODULES) | set(UPSTREAM_READERS)
    for name, tree in _trees():
        text = ast.unparse(tree)
        for prefix in banned:
            assert prefix not in text, f"{name} reaches {prefix}"


def test_the_run_capability_never_reaches_a_producer() -> None:
    """A run can only be produced by a clock the caller supplies."""
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    session = MonitoringSession.begin(
        clock=FixedClock(instant=instant),
        config=MonitoringConfig(),
        monitor=NullMonitor(),
        label="x",
    )
    assert session.run.started_at == instant
    report = session.end()
    assert report.ended_at == instant  # no ambient clock was consulted


def test_a_run_is_complete_before_anything_is_observed() -> None:
    session = MonitoringSession.begin(
        clock=FixedClock(instant=START), config=MonitoringConfig(), monitor=NullMonitor()
    )
    assert set(session.health_map()) == set(MonitoredComponent)
    report = session.end()
    assert set(report.health_map()) == set(MonitoredComponent)


# --- failure containment ----------------------------------------------------


class _FlakyMonitor:
    """Fails on the nth write, succeeds otherwise."""

    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.writes = 0

    def record(self, event: HealthEvent) -> None:
        self.writes += 1
        if self.writes == self.fail_on:
            raise RuntimeError("downstream broke")

    def record_metric(self, metric: object) -> None:
        self.writes += 1
        if self.writes == self.fail_on:
            raise RuntimeError("downstream broke")


def test_a_downstream_failure_never_reaches_the_caller() -> None:
    session = MonitoringSession.begin(
        clock=FixedClock(instant=START),
        config=MonitoringConfig(),
        monitor=_FlakyMonitor(fail_on=1),
    )
    for _ in range(5):
        session.record(
            build_health_event(
                component=MonitoredComponent.DELIVERY,
                code=HealthCode.DELIVERY_TIMEOUT,
                observed_at=START,
            )
        )
    session.record_metric(CounterMetric(name="c").increment(1))
    report = session.end()
    assert report.self_failures >= 1


def test_containment_does_not_swallow_the_truth() -> None:
    session = MonitoringSession.begin(
        clock=FixedClock(instant=START),
        config=MonitoringConfig(),
        monitor=_FlakyMonitor(fail_on=2),
    )
    session.record(
        build_health_event(
            component=MonitoredComponent.DATA_INTEGRITY,
            code=HealthCode.DATA_CONFLICTING_DUPLICATE,
            observed_at=START,
        )
    )
    report = session.end()
    assert report.observed_events == 3
    assert report.state(MonitoredComponent.DATA_INTEGRITY).value == "failing"


def test_one_failed_write_does_not_disable_the_monitor() -> None:
    monitor = _FlakyMonitor(fail_on=2)
    session = MonitoringSession.begin(
        clock=FixedClock(instant=START),
        config=MonitoringConfig(),
        monitor=monitor,
    )
    event = build_health_event(
        component=MonitoredComponent.DELIVERY, code=HealthCode.DELIVERY_TIMEOUT, observed_at=START
    )
    session.record(event)  # this write fails and is contained
    session.record(event)  # the next write is still attempted...
    assert monitor.writes == 3  # ...and succeeds
    assert session.self_failures == 1


def test_self_failures_are_reported_not_silent() -> None:
    session = MonitoringSession.begin(
        clock=FixedClock(instant=START),
        config=MonitoringConfig(),
        monitor=_FlakyMonitor(fail_on=1),
    )
    report = session.end()
    # RUN_STARTED from begin fails; RUN_COMPLETED from end succeeds.
    assert report.self_failures == 1
    assert report.state(MonitoredComponent.MONITORING).value == "failing"
    assert any(
        aggregate.code is HealthCode.MONITOR_INTERNAL_FAILURE for aggregate in report.aggregates
    )


def test_the_report_needs_no_monitor_to_be_complete() -> None:
    session = MonitoringSession.begin(
        clock=FixedClock(instant=START), config=MonitoringConfig(), monitor=NullMonitor()
    )
    session.record(
        build_health_event(
            component=MonitoredComponent.RUN_LIFECYCLE,
            code=HealthCode.RUN_COMPLETED,
            observed_at=LATER,
        )
    )
    report = session.end()
    assert report.aggregates  # built from the session's own retained log
    assert report.report_id.startswith("monitoring-report:")
