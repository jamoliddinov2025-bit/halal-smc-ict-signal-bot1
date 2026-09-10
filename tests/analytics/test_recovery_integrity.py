"""Phase 26E integrity: replay-only recovery with a pinned sanctioned surface.

The general analytics scope guards cover this module automatically (they scan
every analytics source); these tests pin the Phase 26E-specific contracts:
the exact sanctioned import surface of ``recovery.py``, its freedom from IO
and clock capability, and the one-way dependency direction
``persistence -> analytics`` (never the reverse).
"""

from __future__ import annotations

import ast
from pathlib import Path

ANALYTICS_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analytics"
RECOVERY_PATH = ANALYTICS_ROOT / "recovery.py"

ALLOWED_EXTERNAL_MODULES = frozenset(
    {
        "__future__",
        "collections",
        "collections.abc",
        "smcsignal.analysis.errors",
        "smcsignal.analysis.signal_engine.models",
    }
)
ALLOWED_RELATIVE_MODULES = frozenset({"ledger", "lifecycle", "observer"})
FORBIDDEN_FRAGMENTS = (
    "persistence",
    "delivery",
    "monitoring",
    "data",
    "telegram",
    "socket",
    "asyncio",
    "threading",
    "concurrent",
    "urllib",
    "http",
    "subprocess",
    "random",
    "logging",
    "time",
    "os",
    "pathlib",
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


def _absolute_imports(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_recovery_imports_only_the_sanctioned_surface() -> None:
    tree = ast.parse(RECOVERY_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level > 0:
            assert node.module in ALLOWED_RELATIVE_MODULES, (
                f"recovery.py relative import {node.module}: recovery composes only the frozen "
                "26A observer, 26B lifecycle, and 26C snapshot surface"
            )
    for module in _absolute_imports(tree):
        assert module in ALLOWED_EXTERNAL_MODULES, (
            f"recovery.py imports {module}: the sanctioned external surface is exactly "
            "smcsignal.analysis.errors, smcsignal.analysis.signal_engine.models, and the "
            "collections stdlib; analytics siblings enter via relative imports only"
        )


def test_recovery_has_no_io_clock_or_forbidden_capability() -> None:
    tree = ast.parse(RECOVERY_PATH.read_text(encoding="utf-8"))
    for module in _imports(tree):
        root = module.split(".")[0]
        for fragment in FORBIDDEN_FRAGMENTS:
            assert root != fragment and not module.startswith(f"smcsignal.{fragment}"), (
                f"recovery.py imports {module}: recovery performs no IO and touches no "
                "persistence, delivery, monitoring, data, clock, or network capability"
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name not in FORBIDDEN_CALLS, f"recovery.py calls {name}"
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_ATTRIBUTES, (
                f"recovery.py reads a wall-clock attribute ({node.attr})"
            )


def test_analytics_never_imports_persistence_direction_stays_one_way() -> None:
    # Phase 26D approved persistence -> analytics. Phase 26E keeps recovery
    # value-driven (frames in, snapshot in, lifecycle out); the store stays
    # the caller-side snapshot source, so analytics must never import back.
    for path in sorted(ANALYTICS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            assert not module.startswith("smcsignal.persistence"), (
                f"{path.name} imports {module}: the dependency direction must remain "
                "persistence -> analytics"
            )


def test_recovery_module_exists_exactly_once_inside_analytics() -> None:
    assert RECOVERY_PATH.is_file()
    matches = [
        path
        for path in (Path(__file__).resolve().parents[2] / "src" / "smcsignal").rglob("*.py")
        if path.name == "recovery.py"
    ]
    assert matches == [RECOVERY_PATH], "recovery lives in exactly one place: the analytics leaf"
