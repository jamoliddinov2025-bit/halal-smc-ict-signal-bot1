"""Phase 29 integrity: tamper rejection, key safety, atomicity, and boundaries.

Disk corruption is rejected exclusively through the canonical document checks
and the frozen constructor; keys can never escape the root; a failed save
never exposes a partial binding; and the declarations leaf imports only its
sanctioned frozen surfaces — never analytics, persistence, series, sessions,
runs, datasets, configurations, delivery, monitoring, or any network/execution
machinery. Production Phase 29 never calls ``run_declared_history``.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes
from smcsignal.declarations import (
    FileRunBindingStore,
    MemoryRunBindingStore,
    binding_bytes,
    load_binding_bytes,
)
from tests.backtest.helpers import configuration, dataset
from tests.declarations.test_run_binding import binding_for


def test_tampered_disk_contents_are_rejected(tmp_path):
    store = FileRunBindingStore(tmp_path / "declarations")
    original = binding_for()
    store.save("run-primary", original)
    target = tmp_path / "declarations" / "run-primary.binding.json"

    flipped = bytearray(target.read_bytes())
    flipped[len(flipped) // 2] ^= 0x01
    target.write_bytes(bytes(flipped))
    with pytest.raises(AnalysisInputError):
        store.load("run-primary")

    content = binding_bytes(original)
    target.write_bytes(content[: len(content) // 2])  # truncation
    with pytest.raises(AnalysisInputError):
        store.load("run-primary")

    target.write_bytes(content + b"corruption")  # append
    with pytest.raises(AnalysisInputError):
        store.load("run-primary")

    target.write_bytes(content)  # healing restores the exact binding
    assert store.load("run-primary") == original


def _mutated_document(**changes: object) -> bytes:
    document = json.loads(binding_bytes(binding_for()))
    document.update(changes)
    return canonical_bytes(document)


@pytest.mark.parametrize(
    "mutation",
    [
        {"methodology": "declared-run-binding:v9"},
        {"kind": "something-else"},
        {"binding_digest": "binding:0000"},
        {"dataset_key": "dataset-forged"},
        {"configuration_key": "pipeline-forged"},
        {"ledger_key": "ledger-forged"},
        {
            "dataset_digest": "dataset:" + ("ab" * 32),
        },
        {
            "configuration_digest": "configuration:" + ("cd" * 32),
        },
    ],
    ids=[
        "wrong-methodology",
        "wrong-kind",
        "digest-tamper",
        "dataset-key-tamper",
        "configuration-key-tamper",
        "ledger-key-tamper",
        "dataset-identity-tamper",
        "configuration-identity-tamper",
    ],
)
def test_schema_digest_key_and_identity_tampering_is_rejected(mutation) -> None:
    with pytest.raises(AnalysisInputError):
        load_binding_bytes(_mutated_document(**mutation))


def test_identity_tampering_fails_the_recomputed_digest() -> None:
    """A schema-valid value change must fail the content digest, never the schema."""

    document = json.loads(binding_bytes(binding_for()))
    document["ledger_key"] = "series-other"
    with pytest.raises(AnalysisInputError, match="content digest"):
        load_binding_bytes(canonical_bytes(document))


def test_structural_tampering_is_rejected() -> None:
    extra = json.loads(binding_bytes(binding_for()))
    extra["unexpected"] = True
    with pytest.raises(AnalysisInputError):
        load_binding_bytes(canonical_bytes(extra))

    missing = json.loads(binding_bytes(binding_for()))
    del missing["dataset_key"]
    with pytest.raises(AnalysisInputError):
        load_binding_bytes(canonical_bytes(missing))

    keys_as_list = json.loads(binding_bytes(binding_for()))
    keys_as_list["dataset_key"] = ["not", "a", "key"]
    with pytest.raises(AnalysisInputError):
        load_binding_bytes(canonical_bytes(keys_as_list))


def test_load_requires_canonical_bytes() -> None:
    for candidate in ("{}", {"not": "bytes"}, None, 42):
        with pytest.raises(AnalysisInputError, match="canonical bytes"):
            load_binding_bytes(candidate)  # type: ignore[arg-type]


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
        b"run-primary",
        None,
    ],
)
def test_unsafe_keys_are_rejected(tmp_path, bad_key):
    store = FileRunBindingStore(tmp_path / "declarations")
    with pytest.raises(AnalysisInputError):
        store.save(bad_key, binding_for())  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.load(bad_key)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.contains(bad_key)  # type: ignore[arg-type]
    assert list((tmp_path / "declarations").glob("*")) == []


def test_valid_keys_with_internal_dots_dashes_colons_are_accepted(tmp_path):
    store = FileRunBindingStore(tmp_path / "declarations")
    for key in ("BTCUSDT-15m", "run.v2", "btcusdt:15m:primary", "a.b-c:d"):
        store.save(key, binding_for())
        assert store.load(key) == binding_for()


def test_only_declared_run_bindings_can_be_saved(tmp_path):
    for candidate in (
        FileRunBindingStore(tmp_path / "declarations"),
        MemoryRunBindingStore(),
    ):
        for forbidden in (
            dataset(),
            configuration(),
            b"bytes",
            {"not": "a binding"},
            42,
            "DeclaredRunBinding",
        ):
            with pytest.raises(AnalysisInputError, match="DeclaredRunBinding"):
                candidate.save("run-primary", forbidden)  # type: ignore[arg-type]
        with pytest.raises(AnalysisInputError, match="DeclaredRunBinding"):
            binding_bytes(forbidden)  # type: ignore[arg-type]


def test_save_failure_before_replace_keeps_previous_binding_intact(tmp_path, monkeypatch):
    """Injected failure BEFORE os.replace: A intact, no partial, B savable later."""

    store = FileRunBindingStore(tmp_path / "declarations")
    binding_a = binding_for()
    binding_b = binding_for(dataset(symbol="ETHUSDT"), dataset_key="dataset-eth")
    assert binding_bytes(binding_a) != binding_bytes(binding_b)
    store.save("run-primary", binding_a)
    files_before = sorted(path.name for path in (tmp_path / "declarations").iterdir())

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("run-primary", binding_b)

    assert store.load("run-primary") == binding_a  # previous binding loads
    target = tmp_path / "declarations" / "run-primary.binding.json"
    assert target.read_bytes() == binding_bytes(binding_a)  # no partial target
    assert sorted(path.name for path in (tmp_path / "declarations").iterdir()) == files_before
    store.save("run-primary", binding_b)  # a later save works normally
    assert store.load("run-primary") == binding_b


def test_failed_save_into_a_fresh_key_leaves_no_residue(tmp_path, monkeypatch):
    store = FileRunBindingStore(tmp_path / "declarations")

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("brand-new", binding_for())

    assert list((tmp_path / "declarations").glob("*")) == []
    assert store.load("brand-new") is None and not store.contains("brand-new")
    store.save("brand-new", binding_for())
    assert store.contains("brand-new")


DECLARATIONS_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "declarations"


def test_declarations_leaf_imports_only_sanctioned_modules() -> None:
    allowed_stdlib = {
        "__future__",
        "dataclasses",
        "json",
        "os",
        "pathlib",
        "re",
        "typing",
    }
    allowed_smcsignal = {
        "smcsignal.analysis.errors",
        "smcsignal.analysis.liquidity.evidence",
    }
    forbidden_prefixes = (
        "smcsignal.analytics",
        "smcsignal.persistence",
        "smcsignal.series",
        "smcsignal.sessions",
        "smcsignal.runs",
        "smcsignal.datasets",
        "smcsignal.configurations",
        "smcsignal.delivery",
        "smcsignal.monitoring",
        "smcsignal.data",
        "smcsignal.cli",
    )
    for path in sorted(DECLARATIONS_ROOT.rglob("*.py")):
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
                    assert module in allowed_smcsignal, (
                        f"{path.name} imports {module}: declarations consumes only analysis "
                        "errors and the evidence canon"
                    )
                else:
                    assert root in allowed_stdlib, f"{path.name} imports unexpected {module}"
                assert not module.startswith(forbidden_prefixes)
        source = path.read_text(encoding="utf-8")
        assert "run_declared_history" not in source
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("print", "input", "exec", "eval", "__import__", "open")
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                assert "Telegram" not in identifier and "Delivery" not in identifier
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("now", "utcnow", "today", "sleep")
            if isinstance(node, ast.Name):
                assert node.id not in {"random", "socket", "asyncio", "threading", "subprocess"}


def test_nothing_outside_declarations_imports_declarations() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src" / "smcsignal"
    for path in sorted(source_root.rglob("*.py")):
        if path.is_relative_to(DECLARATIONS_ROOT):
            continue
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
                assert not module.startswith("smcsignal.declarations"), (
                    f"{path} imports declarations: the store is a leaf consumed only by callers"
                )


def test_declarations_is_a_single_module() -> None:
    names = sorted(path.name for path in DECLARATIONS_ROOT.rglob("*.py"))
    assert names == ["__init__.py", "run_binding.py"]


def test_facade_exports_the_sanctioned_surface() -> None:
    import smcsignal
    import smcsignal.declarations as declarations_package

    assert declarations_package.__all__ == [
        "DeclaredRunBinding",
        "FileRunBindingStore",
        "MemoryRunBindingStore",
        "RunBindingStore",
        "binding_bytes",
        "load_binding_bytes",
    ]
    assert smcsignal.__all__ == ["__version__"]


def test_production_phase29_never_writes_other_store_documents() -> None:
    for path in sorted(DECLARATIONS_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert ".dataset.json" not in source
        assert ".configuration.json" not in source
        assert ".ledger.json" not in source
        assert "dataset_bytes" not in source
        assert "configuration_bytes" not in source
        assert "ledger_bytes" not in source
