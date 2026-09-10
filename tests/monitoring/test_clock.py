"""Phase 25B-1 clock tests: injected time only, deterministic, never ambient."""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import smcsignal
from smcsignal.monitoring import Clock, FixedClock, MonitoringInputError, SystemClock

MONITORING = Path(smcsignal.__file__).resolve().parent / "monitoring"

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_system_clock_returns_an_aware_instant_and_a_float() -> None:
    clock = SystemClock()
    instant = clock.wall_clock()
    assert instant.tzinfo is not None
    assert instant.utcoffset() is not None
    assert isinstance(clock.monotonic(), float)


def test_system_clock_is_monotonic_non_decreasing() -> None:
    clock = SystemClock()
    assert clock.monotonic() <= clock.monotonic()


def test_fixed_clock_is_exactly_reproducible() -> None:
    clock = FixedClock(instant=NOW, monotonic_seconds=5.0)
    assert clock.wall_clock() == NOW
    assert clock.monotonic() == 5.0
    assert clock.wall_clock() == clock.wall_clock()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"instant": datetime(2026, 1, 1, 12, 0)},  # naive
        {"instant": NOW, "monotonic_seconds": float("nan")},
        {"instant": NOW, "monotonic_seconds": float("inf")},
        {"instant": NOW, "monotonic_seconds": True},
        {"instant": NOW, "monotonic_seconds": "5"},
    ],
)
def test_fixed_clock_rejects_unsafe_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(MonitoringInputError):
        FixedClock(**kwargs)  # type: ignore[arg-type]


def test_advance_returns_a_new_clock_and_never_mutates() -> None:
    clock = FixedClock(instant=NOW, monotonic_seconds=10.0)
    advanced = clock.advance(seconds=60.0, monotonic_seconds=60.0)
    assert advanced.wall_clock() == datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    assert advanced.monotonic() == 70.0
    # The original clock still reports its own readings.
    assert clock.wall_clock() == NOW
    assert clock.monotonic() == 10.0


def test_advance_may_simulate_a_backwards_step() -> None:
    # Negative deltas exist so a clock regression can be provoked deliberately;
    # detecting and reporting it is the monitor's job, not the clock's.
    clock = FixedClock(instant=NOW, monotonic_seconds=10.0)
    stepped = clock.advance(seconds=-120.0, monotonic_seconds=-1.0)
    assert stepped.wall_clock() < clock.wall_clock()
    assert stepped.monotonic() < clock.monotonic()


def test_advance_rejects_non_finite_deltas() -> None:
    clock = FixedClock(instant=NOW)
    with pytest.raises(MonitoringInputError):
        clock.advance(seconds=float("inf"))
    with pytest.raises(MonitoringInputError):
        clock.advance(monotonic_seconds=float("nan"))


def test_both_clocks_satisfy_the_protocol() -> None:
    assert isinstance(SystemClock(), Clock)
    assert isinstance(FixedClock(instant=NOW), Clock)


def test_only_the_clock_module_reads_ambient_time() -> None:
    """No monitoring computation may read the wall clock or monotonic time directly.

    This is what keeps a report reproducible under an injected clock and immune
    to an NTP step: exactly one module is allowed to touch the operating system.
    """
    banned = ("datetime.now(", "utcnow(", "time.monotonic(", "time.time(", "perf_counter(")
    offenders: list[str] = []
    for path in sorted(MONITORING.rglob("*.py")):
        if path.name == "clock.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in banned):
            offenders.append(path.name)
    assert offenders == []


def test_clock_module_imports_no_third_party_module() -> None:
    tree = ast.parse((MONITORING / "clock.py").read_text(encoding="utf-8"))
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            assert node.module.split(".")[0] in allowed, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
