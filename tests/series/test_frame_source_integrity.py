"""Phase 26F integrity: a pinned sanctioned surface for the frame source.

The frame source is frames in (a declared dataset), frames out (Phase 17
publications): it must import only the frozen replay machinery and stdlib,
touch no ledger/persistence/analytics/delivery/monitoring capability, perform
no IO or clock reads, and grow no facade attribute.
"""

from __future__ import annotations

import ast
from pathlib import Path

import smcsignal

SERIES_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "series"
FRAME_SOURCE = SERIES_ROOT / "frame_source.py"

ALLOWED_EXTERNAL_MODULES = frozenset(
    {
        "__future__",
        "smcsignal.analysis.backtest",
        "smcsignal.analysis.provenance",
        "smcsignal.analysis.signal_engine.models",
    }
)
FORBIDDEN_FRAGMENTS = (
    "analytics",
    "persistence",
    "delivery",
    "monitoring",
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


def test_series_imports_only_the_sanctioned_surface() -> None:
    for path in sorted(SERIES_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level > 0:
                raise AssertionError(f"{path.name}: relative imports stay inside the package")
        for module in _imports(tree):
            assert module in ALLOWED_EXTERNAL_MODULES or module.startswith("smcsignal.series"), (
                f"{path.name} imports {module}: the sanctioned surface is the frozen Phase 20 "
                "backtest machinery, the series provenance and Phase 17 frame models, and stdlib"
            )


def test_series_has_no_io_clock_network_or_forbidden_capability() -> None:
    for path in sorted(SERIES_ROOT.rglob("*.py")):
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


def test_series_is_not_a_consumer_of_the_analytics_arc() -> None:
    # Frames flow INTO the analytics arc through callers; the frame source
    # never imports analytics, persistence, delivery, or monitoring back.
    for path in sorted(SERIES_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            for forbidden in ("smcsignal.analytics", "smcsignal.persistence"):
                assert not module.startswith(forbidden), f"{path.name} imports {module}"


def test_the_top_level_facade_is_not_grown() -> None:
    assert smcsignal.__all__ == ["__version__"]
    assert "series" not in smcsignal.__all__


def test_frame_source_lives_in_exactly_one_place() -> None:
    assert FRAME_SOURCE.is_file()
    matches = [
        path
        for path in (Path(__file__).resolve().parents[2] / "src" / "smcsignal").rglob(
            "frame_source.py"
        )
    ]
    assert matches == [FRAME_SOURCE]
