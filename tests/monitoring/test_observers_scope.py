"""Phase 25B-4 boundary tests: the observer is inert, and it stays a leaf.

Phase 25B-4 adds the first monitoring module that reads another layer's output.
That makes the outward edge worth pinning precisely: which modules may be read,
what the observer is forbidden to do with what it reads, and the fact that the
result is values rather than actions.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext
from pathlib import Path

import pytest

import smcsignal
from smcsignal.data.models import OHLCV, OHLCVBatch, ValidationReport
from smcsignal.monitoring import (
    DataObservation,
    FixedClock,
    HealthCode,
    HealthState,
    MarketDataObserver,
    MonitoredComponent,
    MonitoringConfig,
    MonitoringSession,
    RecordingMonitor,
)
from smcsignal.monitoring.observers import __all__ as observers_public

MONITORING = Path(smcsignal.__file__).resolve().parent / "monitoring"
OBSERVERS = MONITORING / "observers"
DATA_OBSERVER = OBSERVERS / "data.py"
BASE = datetime(2026, 1, 1, tzinfo=UTC)

# Calls that would turn an observer into a writer or a sender. Appending to a
# local result list is how the observer builds its own value, and a metric's own
# ``record`` returns a new metric, so neither is a write.
WRITE_CALLS = ("record_metric", "send", "write", "writelines", "__setattr__")

# The observer has no destination handle at all, which is the structural reason
# it cannot forward, retry, or otherwise act on what it reads.
SINK_NAMES = ("monitor", "session", "sink", "logger")

# The published inputs. A method call on one of these would be a mutation of
# someone else's record, which is the thing an observer must never do.
INPUT_NAMES = {
    "batch",
    "candle",
    "candles",
    "current",
    "error",
    "previous",
    "report",
}
INPUT_MUTATORS = (
    "append",
    "clear",
    "insert",
    "pop",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_the_observer_modules_exist_and_nothing_else_does() -> None:
    assert {path.name for path in OBSERVERS.glob("*.py")} == {"__init__.py", "data.py"}


def test_the_observer_package_declares_a_sorted_public_api() -> None:
    assert sorted(observers_public) == list(observers_public)
    assert sorted(observers_public) == [
        "DataObservation",
        "MARKET_DATA_METRICS",
        "MarketDataObserver",
        "series_subject",
    ]


def test_no_observer_module_is_imported_from_outside_monitoring() -> None:
    src = MONITORING.parent
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if MONITORING in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        if "monitoring.observers" in text:
            offenders.append(str(path.relative_to(src)))
    assert offenders == []


def test_the_observer_reads_value_types_but_never_a_provider() -> None:
    """The outward edge is value types and two frozen helpers, nothing more."""
    imported: set[str] = set()
    for node in ast.walk(_tree(DATA_OBSERVER)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    upstream = {
        name for name in imported if name.startswith(("smcsignal.analysis", "smcsignal.data"))
    }
    assert upstream == {
        "smcsignal.analysis.errors",
        "smcsignal.analysis.liquidity.time",
        "smcsignal.analysis.mtf.timeframes",
        "smcsignal.data.errors",
        "smcsignal.data.models",
    }
    for forbidden in (
        "smcsignal.data.base",
        "smcsignal.data.binance",
        "smcsignal.data.config",
        "smcsignal.data.csv",
        "smcsignal.data.factory",
        "smcsignal.data.validation",
    ):
        assert forbidden not in imported, forbidden


def test_the_observer_imports_no_concurrency_or_transport_module() -> None:
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    banned = {"asyncio", "concurrent", "http", "logging", "socket", "threading", "urllib"}
    for path in OBSERVERS.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in allowed, f"{path.name}: {alias.name}"
                    assert root not in banned, f"{path.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                root = node.module.split(".")[0]
                assert root in allowed, f"{path.name}: {node.module}"
                assert root not in banned, f"{path.name}: {node.module}"


def test_the_observer_never_calls_a_writer() -> None:
    for path in OBSERVERS.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call):
                dotted = _dotted(node.func)
                assert dotted.split(".")[-1] not in WRITE_CALLS, f"{path.name} calls {dotted}"
                assert dotted != "object.__setattr__", path.name
                method = node.func
                if (
                    isinstance(method, ast.Attribute)
                    and isinstance(method.value, ast.Name)
                    and method.value.id in INPUT_NAMES
                ):
                    assert method.attr not in INPUT_MUTATORS, (
                        f"{path.name} mutates {method.value.id}"
                    )


def test_the_observer_holds_no_destination_handle() -> None:
    for path in OBSERVERS.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Name):
                assert node.id not in SINK_NAMES, f"{path.name} references {node.id}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in SINK_NAMES, f"{path.name} references {node.attr}"
            elif isinstance(node, ast.arg):
                assert node.arg not in SINK_NAMES, f"{path.name} accepts {node.arg}"


def test_the_observer_holds_no_module_level_mutable_state() -> None:
    for path in OBSERVERS.rglob("*.py"):
        tree = _tree(path)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                if targets == ["__all__"]:
                    continue  # a declared public API list, not state
                assert not isinstance(node.value, (ast.List, ast.Dict, ast.Set)), (
                    f"{path.name} holds a module-level mutable literal"
                )
            if isinstance(node, (ast.ListComp, ast.DictComp, ast.SetComp)):
                raise AssertionError(f"{path.name} holds a module-level mutable literal")
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.Global, ast.Nonlocal)), path.name


def test_the_observer_is_a_frozen_slotted_value_holder() -> None:
    observer = MarketDataObserver()
    assert not hasattr(observer, "__dict__")
    with pytest.raises(FrozenInstanceError):
        observer.config = MonitoringConfig()  # type: ignore[misc]
    assert MarketDataObserver() == MarketDataObserver()


def test_the_observer_accepts_configuration_only() -> None:
    with pytest.raises(TypeError):
        MarketDataObserver(monitor=RecordingMonitor())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        MarketDataObserver(clock=FixedClock(instant=BASE))  # type: ignore[call-arg]


def test_the_observation_is_an_inert_value() -> None:
    observation = DataObservation()
    assert observation.events == ()
    assert observation.metrics == ()
    assert not hasattr(observation, "__dict__")
    with pytest.raises(FrozenInstanceError):
        observation.metrics = ()  # type: ignore[misc]


def test_the_observer_public_api_exposes_no_action_verb() -> None:
    banned = ("send", "retry", "veto", "approve", "reject", "promote", "optimize", "execute")
    for name in observers_public:
        for token in banned:
            assert token not in name.lower(), f"{name} exposes an action"


def test_observing_bends_to_no_ambient_decimal_context() -> None:
    """A ratio is computed under an explicit precision, not the process default."""
    batch = OHLCVBatch(
        candles=(
            OHLCV(
                timestamp=BASE,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            ),
        ),
        report=ValidationReport(input_rows=3, output_rows=1),
    )
    expected = MarketDataObserver().observe_batch(
        batch, timeframe="1h", observed_at=BASE + timedelta(hours=1)
    )
    original = getcontext().prec
    try:
        getcontext().prec = 3
        altered = MarketDataObserver().observe_batch(
            batch, timeframe="1h", observed_at=BASE + timedelta(hours=1)
        )
    finally:
        getcontext().prec = original
    assert altered == expected


def test_the_observer_keeps_no_state_between_observations() -> None:
    batch = OHLCVBatch(
        candles=(_candle(BASE), _candle(BASE + timedelta(hours=2))),
        report=ValidationReport(input_rows=2, output_rows=2),
    )
    observer = MarketDataObserver()
    first = observer.observe_batch(batch, timeframe="1h", observed_at=BASE + timedelta(hours=3))
    observer.observe_batch(_empty_batch(), timeframe="1h", observed_at=BASE + timedelta(hours=3))
    third = observer.observe_batch(batch, timeframe="1h", observed_at=BASE + timedelta(hours=3))
    assert third == first
    assert [event.event_id for event in third.events] == [event.event_id for event in first.events]


def _candle(moment: datetime) -> OHLCV:
    value = Decimal("100")
    return OHLCV(
        timestamp=moment,
        open=value,
        high=value + 1,
        low=value - 1,
        close=value,
        volume=Decimal("1"),
    )


def _empty_batch() -> OHLCVBatch:
    return OHLCVBatch(candles=(), report=ValidationReport(input_rows=0, output_rows=0))


def test_an_observation_is_consumable_without_adaptation() -> None:
    """The observer's output feeds a run unchanged; it needs no privileged access."""
    batch = OHLCVBatch(
        candles=(_candle(BASE), _candle(BASE + timedelta(hours=2))),
        report=ValidationReport(
            input_rows=4, output_rows=2, missing_rows_dropped=2, reordered=True
        ),
    )
    observation = MarketDataObserver().observe_batch(
        batch, timeframe="1h", observed_at=BASE + timedelta(hours=3), symbol="BTCUSDT"
    )
    monitor = RecordingMonitor()
    session = MonitoringSession.begin(
        clock=FixedClock(instant=BASE + timedelta(hours=3)),
        config=MonitoringConfig(),
        monitor=monitor,
    )
    for event in observation.events:
        session.record(event)
    for metric in observation.metrics:
        session.record_metric(metric)
    report = session.end()

    assert report.state(MonitoredComponent.DATA_INTEGRITY) is HealthState.DEGRADED
    assert report.state(MonitoredComponent.MARKET_DATA) is HealthState.DEGRADED
    assert report.metrics.get("market_data.gaps") is not None
    assert {aggregate.code for aggregate in report.aggregates} >= {
        HealthCode.DATA_MISSING_ROWS_DROPPED,
        HealthCode.DATA_REORDERED,
        HealthCode.DATA_GAP,
    }
    assert len(monitor.events()) == len(observation.events) + 2  # plus the run conditions
