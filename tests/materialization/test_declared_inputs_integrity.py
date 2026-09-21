"""Phase 30 integrity: fail-closed resolution, zero writes, no execution, boundaries.

Every missing, corrupt, tampered, replaced, or mismatched declared input makes
materialization raise ``AnalysisInputError`` with nothing returned; a
successful materialization creates, rewrites, or removes no file and calls no
store ``save``; the materialization leaf imports only its sanctioned frozen
surfaces — the Phase 27/28/29 public APIs, the Phase 20 value types, and the
analysis errors — never analytics, persistence, series, sessions, runs,
delivery, monitoring, data providers, CLI, or any IO/clock/network machinery;
and production Phase 30 never imports or calls ``run_declared_history``.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes
from smcsignal.configurations import configuration_bytes
from smcsignal.datasets import dataset_bytes
from smcsignal.declarations import DeclaredRunBinding, binding_bytes
from smcsignal.materialization import VerifiedRunInputs, materialize_declared_inputs
from tests.backtest.helpers import configuration, dataset
from tests.declarations.test_run_binding import (
    CONFIGURATION_KEY,
    DATASET_KEY,
    LEDGER_KEY,
    PIPELINE,
    RUN_KEY,
    binding_for,
    phase27_dataset_digest,
)
from tests.materialization.test_declared_inputs import Stores

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal"
MATERIALIZATION_ROOT = SRC_ROOT / "materialization"
DATASET_FILE = f"{DATASET_KEY}.dataset.json"
CONFIGURATION_FILE = f"{CONFIGURATION_KEY}.configuration.json"
BINDING_FILE = f"{RUN_KEY}.binding.json"


@pytest.fixture(params=["file", "memory"])
def stores(request, tmp_path) -> Stores:
    return Stores(request.param, tmp_path)


@pytest.fixture
def file_stores(tmp_path) -> Stores:
    return Stores("file", tmp_path)


def _tree(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under ``root``: exact bytes plus modification stamp."""

    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------
# Missing inputs fail closed
# --------------------------------------------------------------------------


def test_missing_binding_fails(stores) -> None:
    stores.declare()
    with pytest.raises(AnalysisInputError, match="never-declared"):
        stores.materialize("never-declared")
    with pytest.raises(AnalysisInputError, match="missing"):
        stores.materialize("never-declared")


def test_binding_key_safety_is_the_existing_store_rule(stores) -> None:
    """Keys reach the Phase 29 store unmodified: the closed 26D/27/28/29 rules apply."""

    stores.declare()
    for bad_key in ("", "  ", "a" * 129, "../escape", "a/../b", "a/b", "a\\b", ".hidden"):
        with pytest.raises(AnalysisInputError, match="store key"):
            stores.materialize(bad_key)
    for reserved in ("CON", "nul", "COM1", "lpt9"):
        with pytest.raises(AnalysisInputError, match="reserved device name"):
            stores.materialize(reserved)
    for not_text in (123, b"run-primary", None):
        with pytest.raises(AnalysisInputError, match="must be a string"):
            stores.materialize(not_text)  # type: ignore[arg-type]
    if stores.kind == "file":
        assert sorted(path.name for path in stores.root.iterdir()) == [
            "configurations",
            "datasets",
            "declarations",
        ]


def test_missing_dataset_fails(stores) -> None:
    declaration = binding_for()
    stores.configurations.save(CONFIGURATION_KEY, PIPELINE)
    stores.bindings.save(RUN_KEY, declaration)
    # A dataset under a different key does not satisfy the declared key.
    stores.datasets.save("dataset-other", dataset())

    with pytest.raises(AnalysisInputError, match=f"dataset {DATASET_KEY!r} is missing"):
        stores.materialize()


def test_missing_configuration_fails(stores) -> None:
    declaration = binding_for()
    stores.datasets.save(DATASET_KEY, dataset())
    stores.bindings.save(RUN_KEY, declaration)
    stores.configurations.save("pipeline-other", PIPELINE)

    with pytest.raises(AnalysisInputError, match=f"configuration {CONFIGURATION_KEY!r} is missing"):
        stores.materialize()


def test_missing_dataset_and_configuration_fail_even_with_a_valid_binding(stores) -> None:
    stores.bindings.save(RUN_KEY, binding_for())
    with pytest.raises(AnalysisInputError):
        stores.materialize()


# --------------------------------------------------------------------------
# Identity mismatches fail closed
# --------------------------------------------------------------------------


def test_dataset_digest_mismatch_fails(stores) -> None:
    """A valid binding that pins a different dataset identity is refused."""

    stores.datasets.save(DATASET_KEY, dataset())
    stores.configurations.save(CONFIGURATION_KEY, PIPELINE)
    forged = binding_for(dataset(symbol="ETHUSDT"))  # pins ETH under the BTC key
    stores.bindings.save(RUN_KEY, forged)

    with pytest.raises(AnalysisInputError, match="dataset_digest") as excinfo:
        stores.materialize()
    assert forged.dataset_digest in str(excinfo.value)
    assert phase27_dataset_digest(dataset()) in str(excinfo.value)


def test_configuration_digest_mismatch_fails(stores) -> None:
    stores.datasets.save(DATASET_KEY, dataset())
    stores.configurations.save(CONFIGURATION_KEY, PIPELINE)
    forged = binding_for(pipeline=configuration(threshold=15, horizon=4))
    stores.bindings.save(RUN_KEY, forged)

    with pytest.raises(AnalysisInputError, match="configuration_digest") as excinfo:
        stores.materialize()
    assert forged.configuration_digest in str(excinfo.value)


def test_swapped_dataset_and_configuration_keys_fail(stores) -> None:
    """Right values under the wrong keys are still the wrong declared inputs."""

    stores.declare()
    stores.declare(
        dataset(symbol="ETHUSDT"),
        configuration(threshold=15, horizon=4),
        run_key="run-eth",
        dataset_key="dataset-eth",
        configuration_key="pipeline-strict",
    )
    crossed = DeclaredRunBinding(
        dataset_key="dataset-eth",
        configuration_key=CONFIGURATION_KEY,
        ledger_key=LEDGER_KEY,
        dataset_digest=binding_for().dataset_digest,
        configuration_digest=binding_for().configuration_digest,
    )
    stores.bindings.save("run-crossed", crossed)

    with pytest.raises(AnalysisInputError, match="dataset_digest"):
        stores.materialize("run-crossed")


def test_replacing_a_dataset_under_the_same_key_fails(stores) -> None:
    declaration = stores.declare()
    assert stores.materialize().binding == declaration

    stores.datasets.save(DATASET_KEY, dataset(symbol="ETHUSDT"))
    with pytest.raises(AnalysisInputError, match="dataset_digest"):
        stores.materialize()

    stores.datasets.save(DATASET_KEY, dataset(prices=tuple(range(20, 40))))
    with pytest.raises(AnalysisInputError, match="dataset_digest"):
        stores.materialize()

    stores.datasets.save(DATASET_KEY, dataset())  # restoring the declared content heals
    assert stores.materialize().dataset == dataset()


def test_replacing_a_configuration_under_the_same_key_fails(stores) -> None:
    declaration = stores.declare()
    assert stores.materialize().binding == declaration

    stores.configurations.save(CONFIGURATION_KEY, configuration(threshold=15, horizon=4))
    with pytest.raises(AnalysisInputError, match="configuration_digest"):
        stores.materialize()

    stores.configurations.save(CONFIGURATION_KEY, configuration(horizon=3))
    with pytest.raises(AnalysisInputError, match="configuration_digest"):
        stores.materialize()

    stores.configurations.save(CONFIGURATION_KEY, PIPELINE)
    assert stores.materialize().configuration == PIPELINE


def test_a_resigned_dataset_document_passes_phase27_but_fails_the_binding(file_stores) -> None:
    """Phase 30 adds what Phase 27 alone cannot: identity against the declaration.

    A well-formed Phase 27 document with a different content and a correctly
    recomputed digest restores cleanly through the dataset store — and is
    exactly the case the binding's pinned identity must refuse.
    """

    file_stores.declare()
    target = file_stores.root / "datasets" / DATASET_FILE
    target.write_bytes(dataset_bytes(dataset(symbol="ETHUSDT")))
    assert file_stores.datasets.load(DATASET_KEY) == dataset(symbol="ETHUSDT")

    with pytest.raises(AnalysisInputError, match="dataset_digest"):
        file_stores.materialize()


def test_a_resigned_configuration_document_passes_phase28_but_fails_the_binding(
    file_stores,
) -> None:
    file_stores.declare()
    target = file_stores.root / "configurations" / CONFIGURATION_FILE
    target.write_bytes(configuration_bytes(configuration(threshold=15, horizon=4)))
    assert file_stores.configurations.load(CONFIGURATION_KEY) == configuration(
        threshold=15, horizon=4
    )

    with pytest.raises(AnalysisInputError, match="configuration_digest"):
        file_stores.materialize()


# --------------------------------------------------------------------------
# Corruption and tampering fail closed through the existing store checks
# --------------------------------------------------------------------------


def _corruptions(content: bytes) -> tuple[tuple[str, bytes], ...]:
    flipped = bytearray(content)
    flipped[len(flipped) // 2] ^= 0x01
    return (
        ("bit-flip", bytes(flipped)),
        ("truncation", content[: len(content) // 2]),
        ("append", content + b"corruption"),
        ("empty", b""),
        ("not-json", b"not canonical json"),
    )


def test_tampered_binding_fails(file_stores) -> None:
    declaration = file_stores.declare()
    target = file_stores.root / "declarations" / BINDING_FILE

    for label, corrupt in _corruptions(binding_bytes(declaration)):
        target.write_bytes(corrupt)
        with pytest.raises(AnalysisInputError, match=".") as excinfo:
            file_stores.materialize()
        assert isinstance(excinfo.value, AnalysisInputError), label

    # Schema-valid value changes fail the recomputed binding digest.
    for field, value in (
        ("dataset_key", "dataset-forged"),
        ("configuration_key", "pipeline-forged"),
        ("ledger_key", "series-forged"),
        ("dataset_digest", "dataset:" + "ab" * 32),
        ("configuration_digest", "configuration:" + "cd" * 32),
    ):
        document = json.loads(binding_bytes(declaration))
        document[field] = value
        target.write_bytes(canonical_bytes(document))
        with pytest.raises(AnalysisInputError, match="content digest"):
            file_stores.materialize()

    target.write_bytes(binding_bytes(declaration))  # healing restores resolution
    assert file_stores.materialize().binding == declaration


def test_corrupt_dataset_fails(file_stores) -> None:
    file_stores.declare()
    target = file_stores.root / "datasets" / DATASET_FILE

    for label, corrupt in _corruptions(dataset_bytes(dataset())):
        target.write_bytes(corrupt)
        with pytest.raises(AnalysisInputError, match=".") as excinfo:
            file_stores.materialize()
        assert isinstance(excinfo.value, AnalysisInputError), label

    document = json.loads(dataset_bytes(dataset()))
    document["symbol"] = "ETHUSDT"  # identity change without a recomputed digest
    target.write_bytes(canonical_bytes(document))
    with pytest.raises(AnalysisInputError, match="content digest"):
        file_stores.materialize()

    target.write_bytes(dataset_bytes(dataset()))
    assert file_stores.materialize().dataset == dataset()


def test_corrupt_configuration_fails(file_stores) -> None:
    file_stores.declare()
    target = file_stores.root / "configurations" / CONFIGURATION_FILE

    for label, corrupt in _corruptions(configuration_bytes(PIPELINE)):
        target.write_bytes(corrupt)
        with pytest.raises(AnalysisInputError, match=".") as excinfo:
            file_stores.materialize()
        assert isinstance(excinfo.value, AnalysisInputError), label

    document = json.loads(configuration_bytes(PIPELINE))
    document["live_trading"] = True  # role tampering is refused by Phase 28
    target.write_bytes(canonical_bytes(document))
    with pytest.raises(AnalysisInputError, match="live_trading"):
        file_stores.materialize()

    target.write_bytes(configuration_bytes(PIPELINE))
    assert file_stores.materialize().configuration == PIPELINE


def test_missing_files_after_declaration_fail(file_stores) -> None:
    file_stores.declare()
    for relative in (
        Path("datasets") / DATASET_FILE,
        Path("configurations") / CONFIGURATION_FILE,
        Path("declarations") / BINDING_FILE,
    ):
        target = file_stores.root / relative
        content = target.read_bytes()
        target.unlink()
        with pytest.raises(AnalysisInputError, match="missing"):
            file_stores.materialize()
        target.write_bytes(content)
    assert file_stores.materialize().dataset == dataset()


# --------------------------------------------------------------------------
# Stores that violate their protocol cannot smuggle inputs past the binding
# --------------------------------------------------------------------------


class _KeyIgnoringDatasetStore:
    """A store that answers every key with one dataset: the binding still decides."""

    def __init__(self, value) -> None:
        self._value = value

    def save(self, key, dataset) -> None:
        raise AssertionError("materialization never saves")

    def load(self, key):
        return self._value

    def contains(self, key) -> bool:
        return True


class _KeyIgnoringConfigurationStore(_KeyIgnoringDatasetStore):
    pass


class _KeyIgnoringBindingStore(_KeyIgnoringDatasetStore):
    pass


def test_a_store_returning_a_different_dataset_fails_the_binding(stores) -> None:
    stores.declare()
    wrong = _KeyIgnoringDatasetStore(dataset(symbol="ETHUSDT"))
    with pytest.raises(AnalysisInputError, match="dataset_digest"):
        materialize_declared_inputs(stores.bindings, RUN_KEY, wrong, stores.configurations)
    right = _KeyIgnoringDatasetStore(dataset())
    assert (
        materialize_declared_inputs(stores.bindings, RUN_KEY, right, stores.configurations)
        == stores.materialize()
    )


def test_a_store_returning_a_different_configuration_fails_the_binding(stores) -> None:
    stores.declare()
    wrong = _KeyIgnoringConfigurationStore(configuration(threshold=15, horizon=4))
    with pytest.raises(AnalysisInputError, match="configuration_digest"):
        materialize_declared_inputs(stores.bindings, RUN_KEY, stores.datasets, wrong)


def test_stores_returning_foreign_objects_fail(stores) -> None:
    stores.declare()
    for foreign in (b"bytes", {"not": "a dataset"}, 42, "ReplayDataset", PIPELINE):
        with pytest.raises(AnalysisInputError, match="ReplayDataset"):
            materialize_declared_inputs(
                stores.bindings, RUN_KEY, _KeyIgnoringDatasetStore(foreign), stores.configurations
            )
    for foreign in (b"bytes", {"not": "a configuration"}, 42, dataset()):
        with pytest.raises(AnalysisInputError, match="BacktestConfiguration"):
            materialize_declared_inputs(
                stores.bindings, RUN_KEY, stores.datasets, _KeyIgnoringConfigurationStore(foreign)
            )
    for foreign in (b"bytes", {"not": "a binding"}, 42, dataset(), PIPELINE):
        with pytest.raises(AnalysisInputError, match="DeclaredRunBinding"):
            materialize_declared_inputs(
                _KeyIgnoringBindingStore(foreign), RUN_KEY, stores.datasets, stores.configurations
            )


def test_a_canon_without_an_identity_field_fails_closed(stores, monkeypatch) -> None:
    """If the frozen canon ever stopped embedding its identity, nothing is invented."""

    import smcsignal.materialization.declared_inputs as module

    stores.declare()
    monkeypatch.setattr(module, "dataset_bytes", lambda value: b'{"no": "identity"}')
    with pytest.raises(AnalysisInputError, match="content_digest identity"):
        stores.materialize()
    monkeypatch.setattr(module, "dataset_bytes", lambda value: b"[]")
    with pytest.raises(AnalysisInputError, match="content_digest identity"):
        stores.materialize()


# --------------------------------------------------------------------------
# Zero writes
# --------------------------------------------------------------------------


class _ReadOnly:
    """Wrap a real store so any ``save`` is a test failure; reads pass through."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def save(self, key, value) -> None:
        raise AssertionError(f"materialization attempted to save {key!r}")

    def load(self, key):
        return self._inner.load(key)

    def contains(self, key) -> bool:
        return self._inner.contains(key)


def _forbid_writes(monkeypatch) -> None:
    """Make every filesystem mutation primitive a hard failure."""

    def forbidden(*args, **kwargs):
        raise AssertionError(f"materialization attempted a write: {args!r}")

    real_open = io.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in "wax+"):
            raise AssertionError(f"materialization opened {file!r} for writing ({mode!r})")
        return real_open(file, mode, *args, **kwargs)

    real_os_open = os.open
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & write_flags:
            raise AssertionError(f"materialization opened {path!r} with write flags")
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    for name in (
        "replace",
        "rename",
        "remove",
        "unlink",
        "mkdir",
        "makedirs",
        "rmdir",
        "link",
        "symlink",
        "truncate",
        "utime",
        "chmod",
    ):
        monkeypatch.setattr(os, name, forbidden)
    for name in (
        "write_bytes",
        "write_text",
        "mkdir",
        "touch",
        "unlink",
        "rename",
        "replace",
        "rmdir",
        "chmod",
    ):
        monkeypatch.setattr(Path, name, forbidden)


def test_successful_materialization_creates_no_files(file_stores, monkeypatch) -> None:
    declaration = file_stores.declare()
    before = _tree(file_stores.root)
    assert sorted(before) == [
        f"configurations/{CONFIGURATION_FILE}",
        f"datasets/{DATASET_FILE}",
        f"declarations/{BINDING_FILE}",
    ]

    with monkeypatch.context() as patch:
        _forbid_writes(patch)
        inputs = materialize_declared_inputs(
            _ReadOnly(file_stores.bindings),
            RUN_KEY,
            _ReadOnly(file_stores.datasets),
            _ReadOnly(file_stores.configurations),
        )
        again = materialize_declared_inputs(
            _ReadOnly(file_stores.bindings),
            RUN_KEY,
            _ReadOnly(file_stores.datasets),
            _ReadOnly(file_stores.configurations),
        )

    assert inputs == again
    assert inputs.binding == declaration
    assert _tree(file_stores.root) == before
    assert not list(file_stores.root.rglob("*.partial"))
    assert not list(file_stores.root.rglob("*.tmp"))
    assert not list(file_stores.root.rglob("*.lock"))


def test_failed_materialization_creates_no_files(file_stores, monkeypatch) -> None:
    file_stores.declare()
    file_stores.datasets.save(DATASET_KEY, dataset(symbol="ETHUSDT"))  # mismatch
    before = _tree(file_stores.root)

    with monkeypatch.context() as patch:
        _forbid_writes(patch)
        with pytest.raises(AnalysisInputError, match="dataset_digest"):
            materialize_declared_inputs(
                _ReadOnly(file_stores.bindings),
                RUN_KEY,
                _ReadOnly(file_stores.datasets),
                _ReadOnly(file_stores.configurations),
            )
        with pytest.raises(AnalysisInputError, match="missing"):
            materialize_declared_inputs(
                _ReadOnly(file_stores.bindings),
                "never-declared",
                _ReadOnly(file_stores.datasets),
                _ReadOnly(file_stores.configurations),
            )

    assert _tree(file_stores.root) == before


def test_materialization_never_calls_a_store_save(stores) -> None:
    stores.declare()
    inputs = materialize_declared_inputs(
        _ReadOnly(stores.bindings),
        RUN_KEY,
        _ReadOnly(stores.datasets),
        _ReadOnly(stores.configurations),
    )
    assert inputs == stores.materialize()
    # Store contents are exactly what the caller saved, before and after.
    assert stores.datasets.load(DATASET_KEY) == dataset()
    assert stores.configurations.load(CONFIGURATION_KEY) == PIPELINE
    assert stores.bindings.load(RUN_KEY) == inputs.binding


def test_materialization_does_not_create_missing_store_roots(tmp_path) -> None:
    """Reading from never-created roots neither fails oddly nor creates them."""

    absent = Stores("file", tmp_path / "absent")
    with pytest.raises(AnalysisInputError, match="missing"):
        absent.materialize()
    assert not (tmp_path / "absent").exists()


# --------------------------------------------------------------------------
# No execution: Phase 26H is never imported or called
# --------------------------------------------------------------------------


def test_phase26h_is_never_called(stores, monkeypatch) -> None:
    import smcsignal.runs
    import smcsignal.runs.series_run

    def forbidden(*args, **kwargs):
        raise AssertionError("Phase 30 must not invoke run_declared_history")

    stores.declare()
    monkeypatch.setattr(smcsignal.runs, "run_declared_history", forbidden)
    monkeypatch.setattr(smcsignal.runs.series_run, "run_declared_history", forbidden)
    inputs = stores.materialize()
    assert isinstance(inputs, VerifiedRunInputs)
    assert not hasattr(inputs, "lifecycle")
    assert not hasattr(inputs, "persist")
    assert not hasattr(inputs, "update")
    assert not hasattr(inputs, "recovered_from")


def test_importing_materialization_loads_no_execution_or_runtime_package() -> None:
    """In a fresh interpreter the leaf pulls in none of the execution arc."""

    forbidden = (
        "smcsignal.runs",
        "smcsignal.sessions",
        "smcsignal.series",
        "smcsignal.analytics",
        "smcsignal.persistence",
        "smcsignal.delivery",
        "smcsignal.monitoring",
        "smcsignal.cli",
    )
    script = (
        "import sys\n"
        "import smcsignal.materialization\n"
        f"loaded = [m for m in sys.modules if m.startswith({forbidden!r})]\n"
        "print(repr(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == "[]", completed.stdout


def test_production_phase30_never_names_the_composition_seam() -> None:
    for path in sorted(MATERIALIZATION_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "run_declared_history" not in source
        assert "open_ledger_session" not in source
        assert "series_frames" not in source
        assert "LedgerSession" not in source
        assert "LedgerStore" not in source


# --------------------------------------------------------------------------
# Dependency and import boundaries
# --------------------------------------------------------------------------


def _imports(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def test_materialization_leaf_imports_only_sanctioned_modules() -> None:
    allowed_stdlib = {"__future__", "dataclasses", "json"}
    allowed_smcsignal = {
        "smcsignal.analysis.backtest",
        "smcsignal.analysis.errors",
        "smcsignal.configurations",
        "smcsignal.datasets",
        "smcsignal.declarations",
    }
    forbidden_packages = (
        "smcsignal.analytics",
        "smcsignal.persistence",
        "smcsignal.series",
        "smcsignal.sessions",
        "smcsignal.runs",
        "smcsignal.delivery",
        "smcsignal.monitoring",
        "smcsignal.data",
        "smcsignal.cli",
        "smcsignal.analysis.liquidity.evidence",  # the canon is reached only via 27/28
    )
    forbidden_prefixes = tuple(f"{package}." for package in forbidden_packages)
    forbidden_roots = {
        "os",
        "pathlib",
        "shutil",
        "tempfile",
        "io",
        "hashlib",
        "socket",
        "http",
        "urllib",
        "requests",
        "asyncio",
        "threading",
        "concurrent",
        "multiprocessing",
        "subprocess",
        "random",
        "time",
        "datetime",
        "logging",
        "sqlite3",
        "pickle",
    }
    for path in sorted(MATERIALIZATION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            root = module.split(".")[0]
            if root == "smcsignal":
                assert module in allowed_smcsignal, (
                    f"{path.name} imports {module}: materialization consumes only the Phase "
                    "27/28/29 public APIs, the Phase 20 value types, and analysis errors"
                )
            else:
                assert root in allowed_stdlib, f"{path.name} imports unexpected {module}"
            assert module not in forbidden_packages, f"{path.name} imports {module}"
            assert not module.startswith(forbidden_prefixes), f"{path.name} imports {module}"
            assert root not in forbidden_roots, f"{path.name} imports {module}"
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                assert path.name == "__init__.py", "relative imports only in the facade"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in (
                    "print",
                    "input",
                    "exec",
                    "eval",
                    "compile",
                    "__import__",
                    "open",
                ), f"{path.name} calls {node.func.id}"
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                assert "Telegram" not in identifier and "Delivery" not in identifier
            if isinstance(node, ast.Attribute):
                assert node.attr not in (
                    "save",
                    "replace",
                    "rename",
                    "write_bytes",
                    "write_text",
                    "mkdir",
                    "makedirs",
                    "unlink",
                    "remove",
                    "touch",
                    "now",
                    "utcnow",
                    "today",
                    "sleep",
                ), f"{path.name} uses .{node.attr}"
            if isinstance(node, ast.Name):
                assert node.id not in {
                    "os",
                    "random",
                    "socket",
                    "asyncio",
                    "threading",
                    "subprocess",
                    "sha256",
                    "hashlib",
                    "digest",
                    "canonical_bytes",
                }, f"{path.name} names {node.id}"


def test_production_phase30_defines_no_persistence_format() -> None:
    """No Phase 30 document, suffix, digest prefix, key charset, or store class."""

    for path in sorted(MATERIALIZATION_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for fragment in (
            '.json"',
            ".partial",
            ".dataset.json",
            ".configuration.json",
            ".binding.json",
            ".ledger.json",
            "METHODOLOGY_VERSION",
            "_DIGEST_PREFIX",
            "_KEY_PATTERN",
            "_RESERVED_KEYS",
            "re.compile",
            "load_dataset_bytes",
            "load_configuration_bytes",
            "load_binding_bytes",
            "load_ledger_bytes",
            "ledger_bytes",
        ):
            assert fragment not in source, f"{path.name} contains {fragment!r}"
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name == "VerifiedRunInputs", f"{path.name} defines {node.name}"
                assert not node.name.endswith("Store")


def test_nothing_outside_materialization_imports_materialization() -> None:
    # Phase 31 approved exactly one downstream consumer: the composition
    # adapter accepts an already-verified ``VerifiedRunInputs`` and hands its
    # dataset, configuration, and ledger key to the frozen Phase 26H seam; it
    # never materializes, reloads, or re-verifies anything itself.
    approved_consumer = SRC_ROOT / "composition" / "verified_run.py"
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.is_relative_to(MATERIALIZATION_ROOT) or path == approved_consumer:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            assert not module.startswith("smcsignal.materialization"), (
                f"{path} imports materialization: the leaf is consumed only by callers"
            )


def test_phases_27_28_29_remain_unaware_of_materialization() -> None:
    for package in ("datasets", "configurations", "declarations"):
        for path in sorted((SRC_ROOT / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "materialization" not in source, f"{package}/{path.name} names Phase 30"
            assert "VerifiedRunInputs" not in source


def test_materialization_is_a_single_module() -> None:
    names = sorted(path.name for path in MATERIALIZATION_ROOT.rglob("*.py"))
    assert names == ["__init__.py", "declared_inputs.py"]


def test_facade_exports_the_sanctioned_surface() -> None:
    import smcsignal
    import smcsignal.materialization as materialization_package

    assert materialization_package.__all__ == [
        "VerifiedRunInputs",
        "materialize_declared_inputs",
    ]
    assert smcsignal.__all__ == ["__version__"]
    assert "materialization" not in smcsignal.__all__


def test_verified_inputs_expose_only_the_declared_inputs() -> None:
    public = sorted(name for name in dir(VerifiedRunInputs) if not name.startswith("_"))
    assert public == ["binding", "configuration", "dataset", "ledger_key"]
