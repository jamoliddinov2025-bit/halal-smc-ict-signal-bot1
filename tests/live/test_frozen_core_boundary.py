"""Phase 33 architectural guards: the live boundary stays strictly additive.

These tests scan the actual source with ``ast`` (the established scope-guard
pattern) and prove:

- the frozen analysis core and every frozen infrastructure package never
  import ``smcsignal.live`` (the live boundary is a pure upstream consumer);
- the frozen analysis core never imports Telegram delivery;
- the live package depends only on the standard library and ``smcsignal``,
  and only on the *public* frozen APIs (analysis, delivery, sessions,
  persistence, datasets, data);
- the live package introduced no trading/execution surface and no second
  Telegram transport (no direct HTTP stack, no sink/client re-implementation).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import smcsignal

SRC = Path(smcsignal.__file__).resolve().parent
LIVE = SRC / "live"
ANALYSIS = SRC / "analysis"
DELIVERY = SRC / "delivery"
FROZEN_INFRASTRUCTURE = (
    SRC / "analytics",
    SRC / "composition",
    SRC / "configurations",
    SRC / "data",
    SRC / "datasets",
    SRC / "declarations",
    SRC / "materialization",
    SRC / "monitoring",
    SRC / "persistence",
    SRC / "runs",
    SRC / "series",
    SRC / "sessions",
)

FORBIDDEN_LIVE_TOKENS = (
    "place_order",
    "create_order",
    "cancel_order",
    "api_secret",
    "private_key",
    "ccxt",
    "websocket",
    "aiohttp",
    "httpx",
    "requests",
    "dotenv",
    "leverage",
    "margin",
    "fill_order",
)


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_analysis_never_imports_the_live_boundary() -> None:
    for path in python_files(ANALYSIS):
        for dotted in module_imports(path):
            assert not dotted.startswith("smcsignal.live"), (
                f"{path.relative_to(SRC)} imports {dotted}: the frozen core must "
                "never depend on the live boundary"
            )


def test_analysis_never_imports_telegram_delivery() -> None:
    for path in python_files(ANALYSIS):
        for dotted in module_imports(path):
            assert not dotted.startswith("smcsignal.delivery"), (
                f"{path.relative_to(SRC)} imports {dotted}: no Telegram code in the analysis core"
            )


def test_frozen_infrastructure_never_imports_the_live_boundary() -> None:
    for root in FROZEN_INFRASTRUCTURE:
        for path in python_files(root):
            for dotted in module_imports(path):
                assert not dotted.startswith("smcsignal.live"), (
                    f"{path.relative_to(SRC)} imports {dotted}"
                )


def test_delivery_never_imports_the_live_boundary() -> None:
    for path in python_files(DELIVERY):
        for dotted in module_imports(path):
            assert not dotted.startswith("smcsignal.live"), (
                f"{path.relative_to(SRC)} imports {dotted}: Telegram delivery is "
                "a downstream adapter, never a live importer"
            )


def test_live_service_is_the_only_approved_delivery_consumer() -> None:
    """Mirror of the amended Phase 24 scope guard, from the live side.

    Nothing in ``src`` outside ``delivery/`` imports the delivery layer except
    the Phase 33 live service boundary — the guard's original force is fully
    preserved, and the one approved consumer is exactly the module the task
    designates as the Telegram integration point.
    """

    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if DELIVERY in path.parents or LIVE in path.parents:
            continue
        for dotted in module_imports(path):
            if dotted == "smcsignal.delivery" or dotted.startswith("smcsignal.delivery."):
                offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_only_the_live_service_consumes_the_dataset_store() -> None:
    """Mirror of the amended Phase 27 leaf guard, from the live side.

    Inside the live package, only ``service.py`` may touch the Phase 27
    dataset store (the candle-window persistence boundary); the feed, config,
    and runtime stay persistence-free.
    """

    offenders: list[str] = []
    for path in python_files(LIVE):
        if path.name == "service.py":
            continue
        for dotted in module_imports(path):
            if dotted.startswith("smcsignal.datasets"):
                offenders.append(path.name)
    assert offenders == []


def test_live_imports_only_stdlib_and_smcsignal() -> None:
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for path in python_files(LIVE):
        for dotted in module_imports(path):
            top = dotted.split(".")[0]
            assert top in allowed, f"{path.name} imports {dotted}"


def test_live_depends_on_the_frozen_public_apis() -> None:
    runtime_text = "\n".join(path.read_text(encoding="utf-8") for path in python_files(LIVE))
    assert "from smcsignal.analysis" in runtime_text
    assert "from smcsignal.delivery" in runtime_text
    assert "SignalEngineAnalyzer" in runtime_text
    assert "TelegramDeliveryIntegration" in runtime_text
    assert "open_ledger_session" in runtime_text
    assert "BinancePublicDataProvider" in runtime_text


def test_live_contains_no_trading_or_execution_surface() -> None:
    for path in python_files(LIVE):
        text = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_LIVE_TOKENS:
            assert token not in text, f"{path.name} mentions {token}"


def test_live_introduces_no_second_telegram_transport() -> None:
    for path in python_files(LIVE):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        # No direct HTTP stack: all Telegram IO stays in the frozen Phase 24
        # transport behind the injected boundary.
        for banned in ("urllib.request", "http.client", "urlopen"):
            assert banned not in lowered, f"{path.name} reaches for {banned}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert not node.name.endswith("Sink"), (
                    f"{path.name} defines {node.name}: sinks live in delivery/"
                )
                assert not node.name.endswith("HttpClient"), (
                    f"{path.name} defines {node.name}: the HTTP client lives in "
                    "delivery/telegram/http.py"
                )


def test_live_modules_exist_and_offline_seams_do_not_import_them() -> None:
    # The exact Phase 33 module set exists; nothing else was added under live/.
    expected = {
        "__init__.py",
        "config.py",
        "market_feed.py",
        "runtime.py",
        "service.py",
    }
    assert {path.name for path in LIVE.glob("*.py")} == expected
    # The offline seams stay offline: runs (26H) and series (26F) never import
    # live — already covered above, asserted here explicitly for readability.
    runs_imports = " ".join(
        sorted(name for path in python_files(SRC / "runs") for name in module_imports(path))
    )
    series_imports = " ".join(
        sorted(name for path in python_files(SRC / "series") for name in module_imports(path))
    )
    assert "smcsignal.live" not in runs_imports
    assert "smcsignal.live" not in series_imports
