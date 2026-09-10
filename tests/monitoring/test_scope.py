"""Phase 25B-1 scope audit: leaf package, stdlib only, no producer reachability."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import smcsignal
from smcsignal.monitoring import __all__ as monitoring_public

SRC = Path(smcsignal.__file__).resolve().parent
MONITORING = SRC / "monitoring"

# Network and execution surfaces that must never appear in a monitoring source.
FORBIDDEN_TOKENS = (
    "socket",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "ccxt",
    "websocket",
    "place_order",
    "execute_order",
    "bot_token",
    "leverage",
    "margin",
)

# Producer behaviour monitoring must not reach in Phase 25B-1. It may read value
# types later, but it must never import a coordinator, transport, or governance
# module that could act on a decision.
FORBIDDEN_MODULE_PREFIXES = (
    "smcsignal.analysis.halal_filter",
    "smcsignal.analysis.signal_eligibility",
    "smcsignal.analysis.signal_engine",
    "smcsignal.analysis.improvement",
    "smcsignal.delivery.orchestrator",
    "smcsignal.delivery.sink",
    "smcsignal.delivery.transport",
    "smcsignal.delivery.telegram",
)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_monitoring_imports_only_stdlib_and_smcsignal() -> None:
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for path in MONITORING.rglob("*.py"):
        for dotted in _module_imports(path):
            assert dotted.split(".")[0] in allowed, f"{path.name} imports {dotted}"


def test_monitoring_never_imports_network_or_execution_modules() -> None:
    for path in MONITORING.rglob("*.py"):
        tops = {dotted.split(".")[0] for dotted in _module_imports(path)}
        banned = {"socket", "http", "requests", "httpx", "aiohttp", "urllib", "ccxt"}
        assert not tops & banned, f"{path.name}: {tops & banned}"


def test_monitoring_never_imports_producer_behaviour() -> None:
    for path in MONITORING.rglob("*.py"):
        for dotted in _module_imports(path):
            for banned in FORBIDDEN_MODULE_PREFIXES:
                assert not (dotted == banned or dotted.startswith(banned + ".")), (
                    f"{path.name} imports {dotted}"
                )


def test_monitoring_contains_no_network_or_execution_tokens() -> None:
    for path in MONITORING.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_TOKENS:
            assert token not in text, f"{path.name} mentions {token}"


def test_monitoring_source_avoids_guard_screened_substrings() -> None:
    # Two earlier regression guards assert that no file outside their own
    # package mentions a specific later-phase name anywhere in its source text,
    # including comments and docstrings. Keeping those names out of this package
    # is a hard requirement, not a style preference, and this test makes the
    # failure local and obvious instead of a distant guard error.
    screened = ("rob" + "ustness", "intel" + "ligence")
    for path in MONITORING.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in screened:
            assert token not in text, f"{path.name} mentions {token}"


def test_the_evidence_canon_is_reused_never_copied() -> None:
    """Monitoring reuses the shared canon instead of hand-rolling its own.

    Reusing ``smcsignal.analysis.liquidity.evidence`` executes the analysis
    facade, which reaches the public data-provider module and therefore leaves
    ``socket`` in ``sys.modules``. That is pre-existing baseline behaviour with an
    exact frozen precedent: the Phase 24 delivery core does the same thing from
    its audit module. It is an import side effect, not a capability - no
    monitoring code path opens a connection, no network module is imported
    directly, and the suite-wide socket block would fail any test that tried.
    """
    frozen = (SRC / "delivery" / "audit.py").read_text(encoding="utf-8")
    assert "from smcsignal.analysis.liquidity.evidence import digest" in frozen
    importers: set[str] = set()
    for path in MONITORING.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "liquidity import evidence" not in text and "liquidity.evidence" not in text:
            continue
        importers.add(path.name)
        assert (
            "from smcsignal.analysis.liquidity import evidence" in text
            or "from smcsignal.analysis.liquidity.evidence import" in text
        ), path.name
    # Exactly two modules may reach the canon, and neither may re-implement it.
    assert importers == {"models.py", "serialization.py"}


def test_nothing_outside_monitoring_imports_monitoring() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if MONITORING in path.parents:
            continue
        for dotted in _module_imports(path):
            if dotted == "smcsignal.monitoring" or dotted.startswith("smcsignal.monitoring."):
                offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_health_and_metrics_import_nothing_from_upstream_packages() -> None:
    """The 25B-2 modules must depend only on stdlib and monitoring interior.

    This is the structural reason a transition, a rollup, or a metric cannot
    reach a producer: the modules that compute them have no handle on the data,
    analysis, or delivery layers at all.
    """
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for name in (
        "clock.py",
        "config.py",
        "health.py",
        "metrics.py",
        "monitor.py",
        "report.py",
        "session.py",
    ):
        for dotted in _module_imports(MONITORING / name):
            assert dotted.split(".")[0] in allowed, f"{name} imports {dotted}"
            for upstream in ("smcsignal.data", "smcsignal.analysis", "smcsignal.delivery"):
                assert not (dotted == upstream or dotted.startswith(upstream + ".")), (
                    f"{name} imports {dotted}"
                )


def test_health_and_metrics_reach_only_monitoring_siblings() -> None:
    for name in (
        "clock.py",
        "config.py",
        "health.py",
        "metrics.py",
        "monitor.py",
        "report.py",
        "session.py",
    ):
        for dotted in _module_imports(MONITORING / name):
            if dotted.startswith("smcsignal."):
                assert dotted.startswith("smcsignal.monitoring."), f"{name} imports {dotted}"


def test_no_unimplemented_monitoring_module_exists() -> None:
    # Phase 25B-4 adds the market-data observer. Signal and delivery observers,
    # and alerting, remain unimplemented.
    present = {path.name for path in MONITORING.glob("*.py")}
    assert present == {
        "__init__.py",
        "clock.py",
        "config.py",
        "errors.py",
        "health.py",
        "metrics.py",
        "models.py",
        "monitor.py",
        "report.py",
        "serialization.py",
        "session.py",
    }
    assert not (MONITORING / "alerting.py").exists()
    observers = {path.name for path in (MONITORING / "observers").glob("*.py")}
    assert observers == {"__init__.py", "data.py"}


def test_the_observer_package_exposes_only_the_market_data_observer() -> None:
    from smcsignal.monitoring import observers

    assert sorted(observers.__all__) == list(observers.__all__)
    assert observers.__all__ == [
        "DataObservation",
        "MARKET_DATA_METRICS",
        "MarketDataObserver",
        "series_subject",
    ]


def test_only_the_documented_modules_reach_upstream_packages() -> None:
    """Monitoring's outward edges are three files, each for a frozen value type.

    This is the structural reason an observer cannot reach a producer: no other
    monitoring module imports the data or analysis layers at all.
    """
    consumers: set[str] = set()
    for path in MONITORING.rglob("*.py"):
        for dotted in _module_imports(path):
            if dotted.startswith(("smcsignal.data", "smcsignal.analysis")):
                consumers.add(path.name)
    assert consumers == {"data.py", "models.py", "serialization.py"}


def test_the_market_data_observer_reads_only_published_value_types() -> None:
    dotted = _module_imports(MONITORING / "observers" / "data.py")
    upstream = {
        name for name in dotted if name.startswith(("smcsignal.data", "smcsignal.analysis"))
    }
    assert upstream == {
        "smcsignal.analysis.errors",
        "smcsignal.analysis.liquidity.time",
        "smcsignal.analysis.mtf.timeframes",
        "smcsignal.data.errors",
        "smcsignal.data.models",
    }


def test_top_level_facade_is_not_grown() -> None:
    assert smcsignal.__all__ == ["__version__"]


def test_public_api_has_no_action_verbs() -> None:
    banned = ("send", "retry", "veto", "approve", "reject", "promote", "optimize", "execute")
    for name in monitoring_public:
        for token in banned:
            assert token not in name.lower(), f"{name} exposes an action"


def test_public_api_is_declared_and_importable() -> None:
    module = sys.modules["smcsignal.monitoring"]
    assert sorted(monitoring_public) == list(monitoring_public)
    for name in monitoring_public:
        assert hasattr(module, name), name


def test_package_declares_no_runtime_dependency() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject


def test_public_api_is_declared_and_importable_after_25b2() -> None:
    module = sys.modules["smcsignal.monitoring"]
    for name in ("rollup", "transition", "clear", "rank", "worse_of", "unobserved"):
        assert name in monitoring_public, name
        assert hasattr(module, name), name
    for name in ("CounterMetric", "DurationMetric", "RatioMetric", "GaugeMetric", "MetricSummary"):
        assert name in monitoring_public, name
        assert hasattr(module, name), name


def test_public_api_is_declared_and_importable_after_25b4() -> None:
    module = sys.modules["smcsignal.monitoring"]
    for name in ("MarketDataObserver", "DataObservation", "MARKET_DATA_METRICS", "series_subject"):
        assert name in monitoring_public, name
        assert hasattr(module, name), name
    assert sorted(monitoring_public) == list(monitoring_public)


def test_public_api_is_declared_and_importable_after_25b3() -> None:
    module = sys.modules["smcsignal.monitoring"]
    for name in (
        "Monitor",
        "NullMonitor",
        "RecordingMonitor",
        "MonitoredRun",
        "MonitoringSession",
        "MonitoringReport",
        "aggregate_events",
        "merge_aggregates",
        "advance_aggregate",
        "run_identity",
        "canonical_record",
        "canonical_bytes",
        "content_digest",
        "require_serializable",
    ):
        assert name in monitoring_public, name
        assert hasattr(module, name), name
    assert sorted(monitoring_public) == list(monitoring_public)
