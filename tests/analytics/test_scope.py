"""Phase 26A scope: analytics is a deterministic, downstream-only leaf.

The analytics package may consume published analysis records (Phase 17/18) and
stdlib value types only. It never imports delivery, monitoring, or data
providers; it never touches a clock, file, socket, or concurrency primitive;
and no upstream package may import it back.
"""

from __future__ import annotations

import ast
from pathlib import Path

ANALYTICS_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analytics"
SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal"

ALLOWED_MODULE_ROOTS = ("smcsignal.analysis", "smcsignal.analytics")
FORBIDDEN_SIBLINGS = ("smcsignal.delivery", "smcsignal.monitoring", "smcsignal.data")
FORBIDDEN_MODULES = (
    "threading",
    "asyncio",
    "concurrent",
    "socket",
    "urllib",
    "http",
    "logging",
    "random",
    "time",
    "subprocess",
)
FORBIDDEN_CALLS = ("open", "print", "input", "exec", "eval", "compile", "__import__")
FORBIDDEN_ATTRIBUTES = ("utcnow", "now", "today")


def _trees() -> list[tuple[str, ast.Module]]:
    return [
        (path.name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(ANALYTICS_ROOT.rglob("*.py"))
    ]


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_analytics_imports_only_analysis_records_and_stdlib() -> None:
    stdlib_modules = {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "hashlib",
        "json",
    }
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.level > 0:
                    continue  # relative imports stay inside smcsignal.analytics
                modules = [node.module]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                if root == "smcsignal":
                    assert module.startswith(ALLOWED_MODULE_ROOTS), (
                        f"{name} imports {module}: analytics is a downstream leaf over "
                        "Phase 17/18 records only"
                    )
                    assert not module.startswith(FORBIDDEN_SIBLINGS), f"{name} imports {module}"
                else:
                    assert root in stdlib_modules, f"{name} imports unexpected {module}"


def test_analytics_never_reads_an_ambient_capability() -> None:
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in FORBIDDEN_MODULES, (
                        f"{name} imports {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_MODULES, (
                    f"{name} imports from {node.module}"
                )
            if isinstance(node, ast.Call):
                target = _dotted(node.func).split(".")[-1]
                assert target not in FORBIDDEN_CALLS, f"{name} calls {target}"
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_ATTRIBUTES, (
                    f"{name} reads a wall-clock attribute ({node.attr})"
                )


def test_no_upstream_package_imports_analytics() -> None:
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if ANALYTICS_ROOT in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                assert not module.startswith("smcsignal.analytics"), (
                    f"{path.relative_to(SRC_ROOT)} imports {module}: analytics must stay "
                    "a downstream leaf"
                )
