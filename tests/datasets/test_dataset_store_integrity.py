"""Phase 27 integrity: tamper rejection, key safety, atomicity, and boundaries.

Disk corruption is rejected exclusively through the canonical document checks
and the frozen constructors; keys can never escape the root; a failed save
never exposes a partial dataset; and the datasets leaf imports only its
sanctioned frozen surfaces — never analytics, persistence, series, sessions,
runs, delivery, monitoring, or any network/execution machinery.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes
from smcsignal.datasets import (
    FileDatasetStore,
    MemoryDatasetStore,
    dataset_bytes,
    load_dataset_bytes,
)
from tests.backtest.helpers import dataset


def test_tampered_disk_contents_are_rejected(tmp_path):
    store = FileDatasetStore(tmp_path / "datasets")
    original = dataset()
    store.save("series", original)
    target = tmp_path / "datasets" / "series.dataset.json"

    flipped = bytearray(target.read_bytes())
    flipped[len(flipped) // 2] ^= 0x01
    target.write_bytes(bytes(flipped))
    with pytest.raises(AnalysisInputError):
        store.load("series")

    content = dataset_bytes(original)
    target.write_bytes(content[: len(content) // 2])  # truncation
    with pytest.raises(AnalysisInputError):
        store.load("series")

    target.write_bytes(content + b"corruption")  # append
    with pytest.raises(AnalysisInputError):
        store.load("series")

    target.write_bytes(content)  # healing restores the exact dataset
    assert store.load("series") == original


def _mutated_document(**changes: object) -> bytes:
    document = json.loads(dataset_bytes(dataset()))
    document.update(changes)
    return canonical_bytes(document)


@pytest.mark.parametrize(
    "mutation",
    [
        {"methodology": "declared-dataset:v9"},
        {"kind": "something-else"},
        {"symbol": "ETHUSDT"},  # identity change breaks the recomputed digest
        {"content_digest": "dataset:0000"},
    ],
    ids=["wrong-methodology", "wrong-kind", "identity-tamper", "digest-tamper"],
)
def test_schema_and_digest_tampering_is_rejected(mutation) -> None:
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(_mutated_document(**mutation))


def test_structural_tampering_is_rejected() -> None:
    document = json.loads(dataset_bytes(dataset()))

    extra = dict(document)
    extra["unexpected"] = True
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(canonical_bytes(extra))

    missing = dict(document)
    del missing["higher_candles"]
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(canonical_bytes(missing))

    empty_candles = dict(document)
    empty_candles["candles"] = []
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(canonical_bytes(empty_candles))

    numeric_price = json.loads(dataset_bytes(dataset()))
    numeric_price["candles"][0]["open"] = 20
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(canonical_bytes(numeric_price))

    schema_valid_but_wrong = json.loads(dataset_bytes(dataset()))
    schema_valid_but_wrong["candles"][0]["volume"] = "999"
    with pytest.raises(AnalysisInputError, match="content digest"):
        load_dataset_bytes(canonical_bytes(schema_valid_but_wrong))

    naive_timestamp = json.loads(dataset_bytes(dataset()))
    naive_timestamp["candles"][0]["timestamp"] = "2024-01-01T08:00:00"
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(canonical_bytes(naive_timestamp))

    garbage_timestamp = json.loads(dataset_bytes(dataset()))
    garbage_timestamp["candles"][0]["timestamp"] = "not-a-date"
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(canonical_bytes(garbage_timestamp))

    higher_as_list = json.loads(dataset_bytes(dataset()))
    higher_as_list["higher_candles"] = []
    with pytest.raises(AnalysisInputError):
        load_dataset_bytes(canonical_bytes(higher_as_list))


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
    store = FileDatasetStore(tmp_path / "datasets")
    with pytest.raises(AnalysisInputError):
        store.save(bad_key, dataset())  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.load(bad_key)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.contains(bad_key)  # type: ignore[arg-type]
    assert list((tmp_path / "datasets").glob("*")) == []


def test_valid_keys_with_internal_dots_dashes_colons_are_accepted(tmp_path):
    store = FileDatasetStore(tmp_path / "datasets")
    for key in ("BTCUSDT-15m", "dataset.v2", "btcusdt:15m:primary", "a.b-c:d"):
        store.save(key, dataset())
        assert store.load(key) == dataset()


def test_only_replay_datasets_can_be_saved(tmp_path):
    candle = dataset().candles[0]
    for candidate in (
        FileDatasetStore(tmp_path / "datasets"),
        MemoryDatasetStore(),
    ):
        for forbidden in (candle, b"bytes", {"not": "a dataset"}, 42, "dataset", (candle,)):
            with pytest.raises(AnalysisInputError, match="ReplayDataset"):
                candidate.save("series", forbidden)  # type: ignore[arg-type]
        with pytest.raises(AnalysisInputError, match="ReplayDataset"):
            dataset_bytes(forbidden)  # type: ignore[arg-type]


def test_save_failure_before_replace_keeps_previous_dataset_intact(tmp_path, monkeypatch):
    """Injected failure BEFORE os.replace: A intact, no partial, B savable later."""

    store = FileDatasetStore(tmp_path / "datasets")
    dataset_a = dataset()
    dataset_b = dataset(symbol="ETHUSDT")
    assert dataset_bytes(dataset_a) != dataset_bytes(dataset_b)
    store.save("series", dataset_a)
    files_before = sorted(path.name for path in (tmp_path / "datasets").iterdir())

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("series", dataset_b)

    assert store.load("series") == dataset_a  # 4. previous dataset loads
    target = tmp_path / "datasets" / "series.dataset.json"
    assert target.read_bytes() == dataset_bytes(dataset_a)  # 5. no partial target
    assert sorted(path.name for path in (tmp_path / "datasets").iterdir()) == files_before  # 6.
    store.save("series", dataset_b)  # 7. a later save works normally
    assert store.load("series") == dataset_b


def test_failed_save_into_a_fresh_key_leaves_no_residue(tmp_path, monkeypatch):
    store = FileDatasetStore(tmp_path / "datasets")

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("brand-new", dataset())

    assert list((tmp_path / "datasets").glob("*")) == []
    assert store.load("brand-new") is None and not store.contains("brand-new")
    store.save("brand-new", dataset())
    assert store.contains("brand-new")


DATASETS_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "datasets"


def test_datasets_leaf_imports_only_sanctioned_modules() -> None:
    allowed_stdlib = {
        "__future__",
        "datetime",
        "decimal",
        "json",
        "os",
        "pathlib",
        "re",
        "typing",
    }
    allowed_smcsignal = {
        "smcsignal.analysis.backtest",
        "smcsignal.analysis.errors",
        "smcsignal.analysis.liquidity.evidence",
        "smcsignal.data",
    }
    for path in sorted(DATASETS_ROOT.rglob("*.py")):
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
                        f"{path.name} imports {module}: datasets consumes only the frozen "
                        "dataset models, the evidence canon, and analysis errors"
                    )
                else:
                    assert root in allowed_stdlib, f"{path.name} imports unexpected {module}"
                assert not module.startswith(
                    (
                        "smcsignal.analytics",
                        "smcsignal.persistence",
                        "smcsignal.series",
                        "smcsignal.sessions",
                        "smcsignal.runs",
                        "smcsignal.delivery",
                        "smcsignal.monitoring",
                    )
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("print", "input", "exec", "eval", "__import__")
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                assert "Telegram" not in identifier and "Delivery" not in identifier
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("now", "utcnow", "today", "sleep")


def test_nothing_outside_datasets_imports_datasets() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src" / "smcsignal"
    for path in sorted(source_root.rglob("*.py")):
        if path.is_relative_to(DATASETS_ROOT):
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
                assert not module.startswith("smcsignal.datasets"), (
                    f"{path} imports datasets: the store is a leaf consumed only by callers"
                )


def test_datasets_is_a_single_module() -> None:
    names = sorted(path.name for path in DATASETS_ROOT.rglob("*.py"))
    assert names == ["__init__.py", "dataset_store.py"]


def test_facade_exports_the_sanctioned_surface() -> None:
    import smcsignal
    import smcsignal.datasets as datasets_package

    assert datasets_package.__all__ == [
        "DatasetStore",
        "FileDatasetStore",
        "MemoryDatasetStore",
        "dataset_bytes",
        "load_dataset_bytes",
    ]
    assert smcsignal.__all__ == ["__version__"]
