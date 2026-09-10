"""Phase 24B scope audit: offline-only, no Telegram/network/order/Phase23 surface."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import smcsignal
from smcsignal.delivery import __all__ as delivery_public

SRC = Path(smcsignal.__file__).resolve().parent
DELIVERY = SRC / "delivery"

FORBIDDEN_TOKENS = (
    "telegram",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "socket",
    "ccxt",
    "websocket",
    "place_order",
    "api_key",
    "webhook",
    "apply_human_decision",
    "CandidateExperimentDef",
    "bot_token",
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


def test_delivery_imports_only_stdlib_and_smcsignal() -> None:
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for path in DELIVERY.glob("*.py"):
        for dotted in _module_imports(path):
            top = dotted.split(".")[0]
            assert top in allowed, f"{path.name} imports {dotted}"


def test_delivery_never_imports_network_trading_or_telegram() -> None:
    banned = {"socket", "http", "requests", "httpx", "aiohttp", "urllib", "ccxt"}
    for path in DELIVERY.glob("*.py"):
        tops = {item.split(".")[0] for item in _module_imports(path)}
        assert not tops & banned, f"{path.name}: {tops & banned}"


def test_delivery_contains_no_forbidden_surface_tokens() -> None:
    for path in DELIVERY.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_TOKENS:
            assert token.lower() not in text, f"{path.name} mentions {token}"


def test_delivery_has_no_not_halal_status_value() -> None:
    for path in DELIVERY.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "NOT_HALAL" not in text, f"{path.name} introduces NOT_HALAL"


def test_delivery_public_api_has_no_trading_or_governance_surface() -> None:
    for banned in (
        "trade",
        "order",
        "approve",
        "reject",
        "promote",
        "candidate",
        "optimize",
        "apply_",
    ):
        assert not any(banned in name.lower() for name in delivery_public), banned


def test_no_earlier_phase_module_imports_delivery() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if DELIVERY in path.parents:
            continue
        for dotted in _module_imports(path):
            if dotted == "smcsignal.delivery" or dotted.startswith("smcsignal.delivery."):
                offenders.append(str(path.relative_to(SRC)))
    assert offenders == []
