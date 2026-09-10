"""Phase 26G integrity: pinned sanctioned surface for the ledger session.

The session composes the frozen analytics lifecycle/snapshot surface with the
frozen persistence store protocol — nothing else. These tests pin the exact
sanctioned import surface, the one-way direction (never series, delivery,
monitoring, or data), the absence of IO/clock/concurrency capability, and the
ungrown facade. The session's own IO is exclusively the Phase 26D store it is
given; no filesystem, network, or clock primitive appears in its sources.
"""

from __future__ import annotations

import ast
from pathlib import Path

import smcsignal

SESSIONS_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "sessions"

ALLOWED_EXTERNAL_MODULES = frozenset(
    {
        "__future__",
        "collections.abc",
        "smcsignal.analysis.errors",
        "smcsignal.analysis.outcome_tracking",
        "smcsignal.analysis.signal_engine.models",
        "smcsignal.analytics",
        "smcsignal.persistence",
    }
)
FORBIDDEN_FRAGMENTS = (
    "series",
    "delivery",
    "monitoring",
    "data",
    "telegram",
    "socket",
    "asyncio",
    "threading",
    "concurrent",
    "multiprocessing",
    "urllib",
    "http",
    "subprocess",
    "random",
    "logging",
    "time",
    "os",
    "pathlib",
    "binance",
)
FORBIDDEN_CALLS = ("open", "print", "input", "exec", "eval", "compile", "__import__")
FORBIDDEN_ATTRIBUTES = ("utcnow", "now", "today")


def _imports(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_sessions_imports_only_the_sanctioned_surface() -> None:
    for path in sorted(SESSIONS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level > 0:
                raise AssertionError(f"{path.name}: relative imports stay inside the package")
        for module in _imports(tree):
            assert module in ALLOWED_EXTERNAL_MODULES or module.startswith("smcsignal.sessions"), (
                f"{path.name} imports {module}: the sanctioned surface is the frozen analytics "
                "and persistence public APIs, frozen Phase 17/18 record types, and stdlib"
            )


def test_sessions_has_no_io_clock_network_or_forbidden_capability() -> None:
    for path in sorted(SESSIONS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            root = module.split(".")[0]
            for fragment in FORBIDDEN_FRAGMENTS:
                assert root != fragment, f"{path.name} imports {module}"
                assert not module.startswith(f"smcsignal.{fragment}."), (
                    f"{path.name} imports {module}"
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                assert name not in FORBIDDEN_CALLS, f"{path.name} calls {name}"
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_ATTRIBUTES, (
                    f"{path.name} reads a wall-clock attribute ({node.attr})"
                )


def test_sessions_never_imports_series_delivery_or_monitoring() -> None:
    # Frames enter as caller-supplied values: the session owns no frame
    # source (smcsignal.series stays a free-standing leaf), and delivery and
    # monitoring remain separate arcs.
    for path in sorted(SESSIONS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            for forbidden in ("smcsignal.series", "smcsignal.delivery", "smcsignal.monitoring"):
                assert not module.startswith(forbidden), f"{path.name} imports {module}"


def test_the_top_level_facade_is_not_grown() -> None:
    assert smcsignal.__all__ == ["__version__"]
    assert "sessions" not in smcsignal.__all__


def test_ledger_session_lives_in_exactly_one_place() -> None:
    expected = SESSIONS_ROOT / "ledger_session.py"
    assert expected.is_file()
    matches = [
        path
        for path in (Path(__file__).resolve().parents[2] / "src" / "smcsignal").rglob(
            "ledger_session.py"
        )
    ]
    assert matches == [expected]
