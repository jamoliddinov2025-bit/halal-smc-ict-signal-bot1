"""Phase 26H integrity: the composition seam's pinned sanctioned surface.

``smcsignal.runs`` may compose only the frozen public boundaries — 26F series,
26G sessions, the 26D store protocol, and Phase 20 backtest value types. It
must not import analytics directly, never touches IO/clock/network/concurrency
primitives, and stays unknown to series, sessions, and analytics: composition
points strictly downward, and no cycle exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

import smcsignal

SMC_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal"
RUNS_ROOT = SMC_ROOT / "runs"

ALLOWED_EXTERNAL_MODULES = frozenset(
    {
        "__future__",
        "smcsignal.analysis.backtest",
        "smcsignal.persistence",
        "smcsignal.series",
        "smcsignal.sessions",
    }
)
FORBIDDEN_FRAGMENTS = (
    "analytics",
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


def test_runs_imports_only_the_sanctioned_surface() -> None:
    for path in sorted(RUNS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level > 0:
                raise AssertionError(f"{path.name}: relative imports stay inside the package")
        for module in _imports(tree):
            assert module in ALLOWED_EXTERNAL_MODULES or module.startswith("smcsignal.runs"), (
                f"{path.name} imports {module}: the sanctioned surface is exactly the 26F "
                "series seam, the 26G session seam, the 26D store protocol, and Phase 20 "
                "backtest value types"
            )


def test_runs_has_no_io_clock_network_or_forbidden_capability() -> None:
    for path in sorted(RUNS_ROOT.rglob("*.py")):
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


def test_runs_imports_analytics_only_through_the_session_seam() -> None:
    # The analytics leaf keeps exactly two sanctioned consumers (persistence
    # and sessions); runs reaches analytics never directly, only via 26G.
    for path in sorted(RUNS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            assert not module.startswith("smcsignal.analytics"), (
                f"{path.name} imports {module}: runs composes analytics only through sessions"
            )


def test_series_sessions_and_analytics_remain_unaware_of_runs() -> None:
    # Composition points strictly downward: no frozen leaf imports runs back,
    # so no dependency cycle can exist.
    for package in ("series", "sessions", "analytics"):
        for path in sorted((SMC_ROOT / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module in _imports(tree):
                assert not module.startswith("smcsignal.runs"), (
                    f"{package}/{path.name} imports {module}"
                )


def test_the_top_level_facade_is_not_grown() -> None:
    assert smcsignal.__all__ == ["__version__"]
    assert "runs" not in smcsignal.__all__


def test_series_run_lives_in_exactly_one_place() -> None:
    expected = RUNS_ROOT / "series_run.py"
    assert expected.is_file()
    matches = [path for path in SMC_ROOT.rglob("series_run.py")]
    assert matches == [expected]
