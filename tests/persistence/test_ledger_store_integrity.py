"""Phase 26D integrity: tamper rejection, key safety, atomicity, and boundaries.

Disk corruption is rejected exclusively through Phase 26C (the store parses
nothing); keys can never escape the root; a failed save never exposes a
partial snapshot; and the persistence leaf imports only the Phase 26C public
API plus stdlib IO — never delivery, monitoring, data, or analytics internals.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analytics import ledger_bytes, snapshot_ledger
from smcsignal.delivery import DeliveryCoordinator, DeliveryState
from smcsignal.delivery.sink import FakeTransportSink
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from tests.analytics.helpers import buy_frames
from tests.analytics.test_ledger import frames_for, lifecycle_for
from tests.outcome_tracking.helpers import FALLING_TAIL


def test_tampered_disk_contents_are_rejected_through_phase26c(tmp_path):
    store = FileLedgerStore(tmp_path / "ledgers")
    snapshot = snapshot_ledger(lifecycle_for("win"))
    store.save("series", snapshot)
    target = tmp_path / "ledgers" / "series.ledger.json"

    original = target.read_bytes()
    flipped = bytearray(original)
    flipped[len(flipped) // 2] ^= 0x01
    target.write_bytes(bytes(flipped))

    with pytest.raises(AnalysisInputError):
        store.load("series")

    target.write_bytes(original[: len(original) // 2])  # truncation
    with pytest.raises(AnalysisInputError):
        store.load("series")

    target.write_bytes(original + b"corruption")  # append
    with pytest.raises(AnalysisInputError):
        store.load("series")

    target.write_bytes(original)  # healing restores the exact snapshot
    assert store.load("series") == snapshot


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "  ",
        "a" * 129,
        "/",
        "/absolute/key",
        "..",
        "../escape",
        "a/../b",
        "a/b",
        "a\\b",
        "a\\..\\b",
        ".hidden",
        "trailing.",
        "-leading-dash",
        "with space",
        "tab\tkey",
        "unicode-é",
        "CON",
        "nul",
        "COM1",
        "lpt9",
        123,
        b"series",
        None,
    ],
)
def test_unsafe_keys_are_rejected(tmp_path, bad_key):

    store = FileLedgerStore(tmp_path / "ledgers")
    snapshot = snapshot_ledger(lifecycle_for("empty"))
    with pytest.raises(AnalysisInputError):
        store.save(bad_key, snapshot)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.load(bad_key)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.contains(bad_key)  # type: ignore[arg-type]
    assert list((tmp_path / "ledgers").glob("*")) == []


def test_valid_keys_with_internal_dots_dashes_colons_are_accepted(tmp_path):
    store = FileLedgerStore(tmp_path / "ledgers")
    snapshot = snapshot_ledger(lifecycle_for("empty"))
    for key in ("BTCUSDT-15m", "series.v2", "btcusdt:15m:primary", "a.b-c:d"):
        store.save(key, snapshot)
        assert store.load(key) == snapshot


def test_only_ledger_snapshots_can_be_saved(tmp_path):

    frames = frames_for(None)
    coordinator = DeliveryCoordinator(FakeTransportSink([DeliveryState.DELIVERED]))
    delivered = coordinator.deliver(buy_frames(frames)[0], "destination:phase26d")
    assert delivered.state is DeliveryState.DELIVERED

    for store in (FileLedgerStore(tmp_path / "ledgers"), MemoryLedgerStore()):
        for forbidden in (delivered, frames[0], b"bytes", {"not": "a snapshot"}, 42):
            with pytest.raises(AnalysisInputError, match="LedgerSnapshot"):
                store.save("series", forbidden)  # type: ignore[arg-type]


def test_save_failure_before_replace_keeps_previous_snapshot_intact(tmp_path, monkeypatch):
    """Injected failure BEFORE os.replace: A intact, no partial, B savable later."""

    store = FileLedgerStore(tmp_path / "ledgers")
    snapshot_a = snapshot_ledger(lifecycle_for("win"))
    snapshot_b = snapshot_ledger(lifecycle_for("loss", FALLING_TAIL))
    assert snapshot_a.snapshot_id != snapshot_b.snapshot_id
    store.save("series", snapshot_a)
    files_before = sorted(path.name for path in (tmp_path / "ledgers").iterdir())

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("series", snapshot_b)

    assert store.load("series") == snapshot_a  # 4. previous snapshot loads
    target = tmp_path / "ledgers" / "series.ledger.json"
    assert target.read_bytes() == ledger_bytes(snapshot_a)  # 5. no partial target
    assert sorted(path.name for path in (tmp_path / "ledgers").iterdir()) == files_before  # 6.
    store.save("series", snapshot_b)  # 7. a later save works normally
    assert store.load("series") == snapshot_b


def test_failed_save_into_a_fresh_key_leaves_no_residue(tmp_path, monkeypatch):
    store = FileLedgerStore(tmp_path / "ledgers")

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("brand-new", snapshot_ledger(lifecycle_for("win")))

    assert list((tmp_path / "ledgers").glob("*")) == []
    assert store.load("brand-new") is None and not store.contains("brand-new")
    store.save("brand-new", snapshot_ledger(lifecycle_for("win")))
    assert store.contains("brand-new")


PERSISTENCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "persistence"


def test_persistence_leaf_imports_only_phase26c_and_stdlib_io() -> None:
    allowed_stdlib = {"__future__", "os", "pathlib", "re", "typing"}
    for path in sorted(PERSISTENCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue
                modules = [node.module] if node.module else []
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                if root == "smcsignal":
                    assert module.startswith(
                        (
                            "smcsignal.analytics",
                            "smcsignal.analysis.errors",
                            "smcsignal.persistence",
                        )
                    ), f"{path.name} imports {module}: persistence consumes Phase 26C only"
                    assert module != "smcsignal.analytics.ledger", (
                        f"{path.name} reaches into analytics internals instead of the public API"
                    )
                else:
                    assert root in allowed_stdlib, f"{path.name} imports unexpected {module}"
                assert not module.startswith(
                    ("smcsignal.delivery", "smcsignal.monitoring", "smcsignal.data")
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("print", "input", "exec", "eval", "__import__")
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                assert "Delivery" not in identifier and "Telegram" not in identifier


def test_persistence_never_parses_ledger_json() -> None:
    for path in sorted(PERSISTENCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name != "json" for alias in node.names), path.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "json", f"{path.name} imports json"
