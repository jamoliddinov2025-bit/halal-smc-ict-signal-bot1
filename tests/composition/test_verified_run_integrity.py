"""Phase 31 integrity: exact delegation, no reloads, no writes, no duplication, one-way.

``execute_verified_run`` calls the frozen Phase 26H seam exactly once with the
verified dataset, the verified configuration, the caller's own store, and the
declared ledger key — nothing else, nothing transformed — and returns the
seam's result unchanged; every exception the seam raises propagates as is;
nothing that is not a Phase 30 ``VerifiedRunInputs`` reaches the seam; the
Phase 27/28/29 stores are never reloaded; the adapter itself writes nothing;
its import pulls in nothing beyond its two declared upstreams; it duplicates
none of the Phase 26F/26G/26H machinery; and the dependency direction stays
materialization → composition → runs, with nothing importing it back.
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
from types import SimpleNamespace

import pytest

import smcsignal.composition.verified_run as adapter_module
import smcsignal.runs
import smcsignal.runs.series_run
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analytics import ledger_bytes, snapshot_ledger
from smcsignal.composition import execute_verified_run
from smcsignal.configurations import FileConfigurationStore, MemoryConfigurationStore
from smcsignal.datasets import FileDatasetStore, MemoryDatasetStore
from smcsignal.declarations import FileRunBindingStore, MemoryRunBindingStore
from smcsignal.materialization import VerifiedRunInputs
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from smcsignal.runs import run_declared_history
from tests.backtest.helpers import configuration, dataset
from tests.composition.test_verified_run import verified_inputs
from tests.declarations.test_run_binding import (
    CONFIGURATION_KEY,
    DATASET_KEY,
    LEDGER_KEY,
    PIPELINE,
    RUN_KEY,
    binding_for,
)
from tests.materialization.test_declared_inputs import Stores
from tests.runs.test_series_run import assert_ledgers_equal

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal"
COMPOSITION_ROOT = SRC_ROOT / "composition"
ADAPTER = COMPOSITION_ROOT / "verified_run.py"
FACADE = COMPOSITION_ROOT / "__init__.py"

# The two upstream boundaries Phase 31 composes, and the frozen leaves that
# must never learn about it.
UPSTREAM_PACKAGES = ("materialization", "runs")
FROZEN_PACKAGES = (
    "analysis",
    "analytics",
    "configurations",
    "data",
    "datasets",
    "declarations",
    "delivery",
    "materialization",
    "monitoring",
    "persistence",
    "runs",
    "series",
    "sessions",
)


class _Seam:
    """A stand-in for ``run_declared_history`` that records every call exactly."""

    def __init__(self, result=None, error=None) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.result = result
        self.error = error

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def seam(monkeypatch) -> _Seam:
    spy = _Seam(result=object())
    monkeypatch.setattr(adapter_module, "run_declared_history", spy)
    return spy


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileLedgerStore(tmp_path / "ledgers")
    return MemoryLedgerStore()


def _tree(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under ``root``: exact bytes plus modification stamp."""

    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _forbid_writes(monkeypatch) -> None:
    """Make every filesystem mutation primitive a hard failure."""

    def forbidden(*args, **kwargs):
        raise AssertionError(f"Phase 31 attempted a filesystem write: {args!r}")

    real_open = io.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in "wax+"):
            raise AssertionError(f"Phase 31 opened {file!r} for writing ({mode!r})")
        return real_open(file, mode, *args, **kwargs)

    real_os_open = os.open
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & write_flags:
            raise AssertionError(f"Phase 31 opened {path!r} with write flags")
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    for name in ("replace", "rename", "remove", "unlink", "mkdir", "makedirs", "rmdir", "link"):
        monkeypatch.setattr(os, name, forbidden)
    for name in ("write_bytes", "write_text", "mkdir", "touch", "unlink", "rename", "replace"):
        monkeypatch.setattr(Path, name, forbidden)


def _imports(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _adapter_tree() -> ast.Module:
    return ast.parse(ADAPTER.read_text(encoding="utf-8"), filename=str(ADAPTER))


# --------------------------------------------------------------------------
# Exact delegation to Phase 26H
# --------------------------------------------------------------------------


def test_phase26h_receives_exactly_the_verified_inputs_and_the_callers_store(seam) -> None:
    inputs = verified_inputs()
    store = MemoryLedgerStore()

    result = execute_verified_run(inputs, store)

    assert len(seam.calls) == 1
    args, kwargs = seam.calls[0]
    assert kwargs == {}
    assert len(args) == 4
    dataset_arg, configuration_arg, store_arg, key_arg = args
    assert dataset_arg is inputs.dataset  # 1. the verified dataset, the same object
    assert configuration_arg is inputs.configuration  # 2. the verified configuration
    assert store_arg is store  # 3. the caller-supplied store, unchanged
    assert key_arg is inputs.ledger_key  # 4. the declared ledger key
    assert key_arg == inputs.binding.ledger_key == LEDGER_KEY
    assert result is seam.result  # 5. the seam's result, returned as is


def test_delegation_uses_the_declared_values_not_defaults(seam) -> None:
    eth = dataset(symbol="ETHUSDT")
    strict = configuration(threshold=15, horizon=4)
    inputs = verified_inputs(eth, strict, ledger_key="series-eth-strict")
    store = FileLedgerStore(Path("/nonexistent/never-touched"))  # never reached by the spy

    execute_verified_run(inputs, store)

    (args, _kwargs), *_ = seam.calls
    assert args == (eth, strict, store, "series-eth-strict")
    assert args[0] is eth and args[1] is strict and args[2] is store


def test_phase26h_is_called_exactly_once_per_execution(seam) -> None:
    inputs = verified_inputs()
    for expected in (1, 2, 3):
        execute_verified_run(inputs, MemoryLedgerStore())
        assert len(seam.calls) == expected


def test_the_real_seam_is_entered_exactly_once(monkeypatch, store) -> None:
    """Counting the frozen 26H function itself, not a stand-in."""

    real = smcsignal.runs.series_run.run_declared_history
    calls: list[tuple] = []
    returned: list[object] = []

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        session = real(*args, **kwargs)
        returned.append(session)
        return session

    monkeypatch.setattr(adapter_module, "run_declared_history", counted)
    inputs = verified_inputs()

    session = execute_verified_run(inputs, store)

    assert len(calls) == 1
    assert calls[0] == ((inputs.dataset, inputs.configuration, store, inputs.ledger_key), {})
    assert session is returned[0]  # the seam's own session object, unchanged


def test_frame_regeneration_happens_exactly_once_per_execution(monkeypatch, store) -> None:
    """Inside the real seam, the frozen 26F regeneration runs once: no second pass."""

    real_frames = smcsignal.runs.series_run.series_frames
    calls: list[tuple] = []

    def counted(*args, **kwargs):
        calls.append(args)
        return real_frames(*args, **kwargs)

    monkeypatch.setattr(smcsignal.runs.series_run, "series_frames", counted)
    inputs = verified_inputs()

    execute_verified_run(inputs, store)

    assert calls == [(inputs.dataset, inputs.configuration)]


def test_result_is_the_seams_session_object_unchanged(store) -> None:
    inputs = verified_inputs()
    session = execute_verified_run(inputs, store)
    assert type(session).__module__ == "smcsignal.sessions.ledger_session"
    assert session.store is store
    assert session.key == inputs.ledger_key
    assert session.config is inputs.configuration.outcome_tracking
    # The live continuation machinery is intact: it is the 26G session itself.
    assert callable(session.update) and callable(session.persist)
    assert session.persist() == snapshot_ledger(session.lifecycle)


def test_the_adapter_binds_the_frozen_seam_object_itself() -> None:
    assert adapter_module.run_declared_history is smcsignal.runs.run_declared_history
    assert adapter_module.run_declared_history is smcsignal.runs.series_run.run_declared_history


def test_executing_leaves_phase26h_untouched(store) -> None:
    before = smcsignal.runs.series_run.run_declared_history
    execute_verified_run(verified_inputs(), store)
    assert smcsignal.runs.series_run.run_declared_history is before
    assert smcsignal.runs.run_declared_history is before
    assert smcsignal.runs.__all__ == ["run_declared_history"]


# --------------------------------------------------------------------------
# Only a Phase 30 VerifiedRunInputs reaches the seam
# --------------------------------------------------------------------------


def test_unverified_look_alikes_are_refused_before_the_seam(seam) -> None:
    """Duck-typed inputs never verified by Phase 30 must not execute."""

    look_alike = SimpleNamespace(
        dataset=dataset(symbol="ETHUSDT"), configuration=PIPELINE, ledger_key=LEDGER_KEY
    )
    binding = binding_for()
    for foreign in (
        look_alike,
        binding,
        dataset(),
        PIPELINE,
        {"dataset": dataset(), "configuration": PIPELINE, "ledger_key": LEDGER_KEY},
        (dataset(), PIPELINE, LEDGER_KEY),
        None,
        "VerifiedRunInputs",
    ):
        with pytest.raises(AnalysisInputError, match="VerifiedRunInputs"):
            execute_verified_run(foreign, MemoryLedgerStore())  # type: ignore[arg-type]
    assert seam.calls == []


def test_phase30_construction_remains_the_only_way_in() -> None:
    """Identity verification is Phase 30's: a mismatched value cannot even be built."""

    with pytest.raises(AnalysisInputError, match="dataset_digest"):
        VerifiedRunInputs(
            binding=binding_for(), dataset=dataset(symbol="ETHUSDT"), configuration=PIPELINE
        )
    source = ADAPTER.read_text(encoding="utf-8")
    for fragment in (
        "dataset_bytes",
        "configuration_bytes",
        "binding_bytes",
        "canonical_bytes",
        "content_digest",
        "configuration_digest",
        "dataset_digest",
        "hashlib",
        "sha256",
        "json",
    ):
        assert fragment not in source, f"the adapter names {fragment!r}: Phase 30 verifies"


# --------------------------------------------------------------------------
# Nothing is reloaded
# --------------------------------------------------------------------------


def _forbid_store_reads(monkeypatch) -> None:
    def forbidden(name):
        def method(self, *args, **kwargs):
            raise AssertionError(f"Phase 31 reloaded through {type(self).__name__}.{name}")

        return method

    for cls in (
        FileDatasetStore,
        MemoryDatasetStore,
        FileConfigurationStore,
        MemoryConfigurationStore,
        FileRunBindingStore,
        MemoryRunBindingStore,
    ):
        for name in ("load", "contains", "save"):
            monkeypatch.setattr(cls, name, forbidden(name))
    import smcsignal.materialization
    import smcsignal.materialization.declared_inputs

    def no_rematerialization(*args, **kwargs):
        raise AssertionError("Phase 31 re-materialized the declared inputs")

    monkeypatch.setattr(
        smcsignal.materialization, "materialize_declared_inputs", no_rematerialization
    )
    monkeypatch.setattr(
        smcsignal.materialization.declared_inputs,
        "materialize_declared_inputs",
        no_rematerialization,
    )


@pytest.mark.parametrize("kind", ["file", "memory"])
def test_dataset_configuration_and_binding_stores_are_not_reloaded(
    tmp_path, monkeypatch, kind
) -> None:
    stores = Stores(kind, tmp_path)
    stores.declare()
    inputs = stores.materialize()  # Phase 30 did all the loading, before this point

    with monkeypatch.context() as patch:
        _forbid_store_reads(patch)
        session = execute_verified_run(inputs, MemoryLedgerStore())
        again = execute_verified_run(inputs, MemoryLedgerStore())

    assert session.key == LEDGER_KEY
    assert_ledgers_equal(session.lifecycle, again.lifecycle)


def test_replacing_stored_inputs_after_verification_does_not_reach_execution(tmp_path) -> None:
    """Execution consumes the verified value, never whatever the stores hold now."""

    stores = Stores("file", tmp_path)
    stores.declare()
    inputs = stores.materialize()

    # Substitute both declared inputs under their keys AFTER verification.
    stores.datasets.save(DATASET_KEY, dataset(symbol="ETHUSDT"))
    stores.configurations.save(CONFIGURATION_KEY, configuration(threshold=15, horizon=4))
    with pytest.raises(AnalysisInputError):
        stores.materialize()  # Phase 30 now refuses the substituted inputs

    session = execute_verified_run(inputs, MemoryLedgerStore())

    assert_ledgers_equal(
        session.lifecycle,
        run_declared_history(dataset(), PIPELINE, MemoryLedgerStore(), LEDGER_KEY).lifecycle,
    )
    assert session.config == PIPELINE.outcome_tracking


def test_adapter_never_names_a_store_operation() -> None:
    tree = _adapter_tree()
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert attributes == {"dataset", "configuration", "ledger_key"}, attributes
    for module in _imports(tree):
        assert not module.startswith(
            ("smcsignal.datasets", "smcsignal.configurations", "smcsignal.declarations")
        ), f"the adapter imports {module}: it must not be able to reload anything"


# --------------------------------------------------------------------------
# Exceptions propagate unchanged
# --------------------------------------------------------------------------


class _SeamFailure(Exception):
    pass


class _SeamInterrupt(BaseException):
    pass


@pytest.mark.parametrize("error_type", [_SeamFailure, AnalysisInputError, OSError, _SeamInterrupt])
def test_seam_exceptions_propagate_as_the_same_object(monkeypatch, error_type) -> None:
    error = error_type("raised inside Phase 26H")
    spy = _Seam(error=error)
    monkeypatch.setattr(adapter_module, "run_declared_history", spy)

    with pytest.raises(error_type) as excinfo:
        execute_verified_run(verified_inputs(), MemoryLedgerStore())

    assert excinfo.value is error  # not wrapped, not re-raised as something else
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is False
    assert len(spy.calls) == 1


def test_a_real_domain_refusal_propagates_and_leaves_the_store_unchanged(store) -> None:
    """The frozen 26G refusal for a different series under the same key is untouched."""

    execute_verified_run(verified_inputs(), store)
    before = ledger_bytes(store.load(LEDGER_KEY))

    foreign = verified_inputs(dataset(symbol="ETHUSDT"))  # same declared ledger key
    with pytest.raises(AnalysisInputError) as excinfo:
        execute_verified_run(foreign, store)

    assert type(excinfo.value) is AnalysisInputError
    assert excinfo.value.__cause__ is None
    assert ledger_bytes(store.load(LEDGER_KEY)) == before
    rerun = execute_verified_run(verified_inputs(), store)
    assert rerun.recovered_from == store.load(LEDGER_KEY)


class _BrokenSaveStore:
    """A caller store whose approved open-time write fails: the failure surfaces as is."""

    def __init__(self) -> None:
        self._inner = MemoryLedgerStore()

    def save(self, key, snapshot) -> None:
        raise OSError("injected store failure")

    def load(self, key):
        return self._inner.load(key)

    def contains(self, key) -> bool:
        return self._inner.contains(key)


def test_a_failing_caller_store_surfaces_unchanged() -> None:
    with pytest.raises(OSError, match="injected store failure") as excinfo:
        execute_verified_run(verified_inputs(), _BrokenSaveStore())
    assert excinfo.value.__cause__ is None


def test_adapter_has_no_exception_handling_at_all() -> None:
    tree = _adapter_tree()
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Try), "the adapter must not catch anything"
        assert not isinstance(node, ast.With), "the adapter must not wrap the call in a context"
        if isinstance(node, ast.Raise):
            assert node.cause is None, "the adapter must not re-raise with a cause"


# --------------------------------------------------------------------------
# Zero writes of its own
# --------------------------------------------------------------------------


def test_the_adapter_itself_performs_no_filesystem_write(seam, monkeypatch) -> None:
    with monkeypatch.context() as patch:
        _forbid_writes(patch)
        result = execute_verified_run(verified_inputs(), MemoryLedgerStore())
    assert result is seam.result
    assert len(seam.calls) == 1


def test_execution_with_a_memory_store_touches_no_file(monkeypatch) -> None:
    with monkeypatch.context() as patch:
        _forbid_writes(patch)
        session = execute_verified_run(verified_inputs(), MemoryLedgerStore())
        again = execute_verified_run(verified_inputs(), MemoryLedgerStore())
    assert_ledgers_equal(session.lifecycle, again.lifecycle)


def test_execution_with_a_file_store_writes_exactly_what_phase26h_writes(tmp_path) -> None:
    """The only file IO is the caller's Phase 26D store, exactly as the seam channels it."""

    inputs = verified_inputs()
    adapted_root = tmp_path / "adapted"
    direct_root = tmp_path / "direct"

    execute_verified_run(inputs, FileLedgerStore(adapted_root))
    run_declared_history(
        inputs.dataset, inputs.configuration, FileLedgerStore(direct_root), inputs.ledger_key
    )

    adapted = {name: content for name, (content, _stamp) in _tree(adapted_root).items()}
    direct = {name: content for name, (content, _stamp) in _tree(direct_root).items()}
    assert adapted == direct
    assert sorted(adapted) == [f"{LEDGER_KEY}.ledger.json"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["adapted", "direct"]
    for root in (adapted_root, direct_root):
        assert not list(root.rglob("*.partial"))
        assert not list(root.rglob("*.tmp"))
        assert not list(root.rglob("*.lock"))


def test_execution_leaves_the_declared_input_stores_byte_identical(tmp_path) -> None:
    stores = Stores("file", tmp_path / "declared")
    stores.declare()
    inputs = stores.materialize()
    before = _tree(tmp_path / "declared")
    assert sorted(before) == [
        f"configurations/{CONFIGURATION_KEY}.configuration.json",
        f"datasets/{DATASET_KEY}.dataset.json",
        f"declarations/{RUN_KEY}.binding.json",
    ]

    execute_verified_run(inputs, FileLedgerStore(tmp_path / "ledgers"))
    execute_verified_run(inputs, FileLedgerStore(tmp_path / "ledgers"))

    assert _tree(tmp_path / "declared") == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["declared", "ledgers"]


def test_adapter_sources_name_no_io_clock_or_persistence_primitive() -> None:
    for path in (ADAPTER, FACADE):
        source = path.read_text(encoding="utf-8")
        for fragment in (
            'json"',
            ".partial",
            ".ledger.json",
            ".dataset.json",
            ".configuration.json",
            ".binding.json",
            "os.replace",
            "re.compile",
            "_KEY_PATTERN",
            "_RESERVED_KEYS",
            "tempfile",
            "NamedTemporaryFile",
            "write_bytes",
            "write_text",
            "mkdir",
            "Path(",
            "open(",
            "print(",
            "datetime",
            "utcnow",
            "time.",
            "random",
            "socket",
            "urllib",
            "http",
            "asyncio",
            "threading",
            "subprocess",
            "logging",
            "Telegram",
            "telegram",
            "Delivery",
            "binance",
            "Binance",
            "websocket",
            "sleep",
        ):
            assert fragment not in source, f"{path.name} contains {fragment!r}"


# --------------------------------------------------------------------------
# Import surface, isolation, and no duplication
# --------------------------------------------------------------------------


def test_adapter_imports_exactly_the_sanctioned_surface() -> None:
    tree = _adapter_tree()
    imported: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raise AssertionError(f"the adapter uses a bare import: {[a.name for a in node.names]}")
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "the adapter uses only absolute imports"
            assert node.module is not None
            imported.setdefault(node.module, set()).update(alias.name for alias in node.names)
    assert imported == {
        "__future__": {"annotations"},
        "smcsignal.analysis.errors": {"AnalysisInputError"},
        "smcsignal.materialization": {"VerifiedRunInputs"},
        "smcsignal.persistence": {"LedgerStore"},
        "smcsignal.runs": {"run_declared_history"},
        "smcsignal.sessions": {"LedgerSession"},
    }, imported


def test_facade_imports_only_the_adapter_module() -> None:
    tree = ast.parse(FACADE.read_text(encoding="utf-8"), filename=str(FACADE))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert len(imports) == 1
    (node,) = imports
    assert isinstance(node, ast.ImportFrom)
    assert node.level == 1 and node.module == "verified_run"
    assert [alias.name for alias in node.names] == ["execute_verified_run"]


def test_adapter_never_imports_the_machinery_phase26h_composes() -> None:
    """Reaching 26F/26G/26C directly would be a second engine; only 26H is called."""

    for module in _imports(_adapter_tree()):
        for forbidden in (
            "smcsignal.series",
            "smcsignal.analytics",
            "smcsignal.analysis.backtest",
            "smcsignal.analysis.outcome_tracking",
            "smcsignal.analysis.signal_engine",
            "smcsignal.analysis.liquidity",
            "smcsignal.data",
            "smcsignal.delivery",
            "smcsignal.monitoring",
            "smcsignal.cli",
        ):
            assert module != forbidden and not module.startswith(forbidden + "."), module


def test_adapter_duplicates_none_of_phase26h() -> None:
    tree = _adapter_tree()
    source = ADAPTER.read_text(encoding="utf-8")
    for fragment in (
        "series_frames",
        "SeriesFrameSource",
        "open_ledger_session",
        "outcome_tracking",
        "OutcomeTrackingConfig",
        "SignalOutcomeLifecycle",
        "AnalyticsObserver",
        "snapshot_ledger",
        "restore_ledger",
        "LedgerSnapshot",
        "HistoricalReplay",
        "recover_lifecycle",
        "RecoveredLedger",
        "load_ledger_bytes",
        "ledger_bytes",
    ):
        assert fragment not in source, f"the adapter names {fragment!r}: 26H already does this"

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    called = sorted(
        node.func.id if isinstance(node.func, ast.Name) else ast.dump(node.func) for node in calls
    )
    assert called == ["AnalysisInputError", "isinstance", "run_declared_history"], called
    seam_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "run_declared_history"
    ]
    assert len(seam_calls) == 1
    (call,) = seam_calls
    assert call.keywords == []
    assert [ast.unparse(argument) for argument in call.args] == [
        "inputs.dataset",
        "inputs.configuration",
        "store",
        "inputs.ledger_key",
    ]
    # The delegated call is the function's return value itself: no post-processing.
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    assert [function.name for function in functions] == ["execute_verified_run"]
    (function,) = functions
    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    assert len(returns) == 1 and returns[0].value is call
    assert not any(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
    assert not any(isinstance(node, (ast.For, ast.While, ast.Lambda)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


def test_adapter_holds_no_module_state_and_no_defaults() -> None:
    tree = _adapter_tree()
    kinds = [type(node).__name__ for node in tree.body]
    assert kinds[0] == "Expr"  # the module docstring
    assert set(kinds[1:]) == {"ImportFrom", "FunctionDef"}, kinds
    (function,) = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert [argument.arg for argument in function.args.args] == ["inputs", "store"]
    assert function.args.defaults == [] and function.args.kw_defaults == []
    assert function.args.vararg is None and function.args.kwarg is None
    assert function.decorator_list == []
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Global, ast.Nonlocal, ast.Assign, ast.AugAssign))
        assert not isinstance(node, (ast.AnnAssign, ast.NamedExpr))


def test_adapter_source_files_are_exactly_the_facade_and_one_module() -> None:
    names = sorted(path.name for path in COMPOSITION_ROOT.rglob("*.py"))
    assert names == ["__init__.py", "verified_run.py"]
    assert COMPOSITION_ROOT.parent == SRC_ROOT
    for reserved in ("series_run.py", "ledger_session.py", "frame_source.py", "declared_inputs.py"):
        assert not (COMPOSITION_ROOT / reserved).exists()


def test_facade_exports_the_sanctioned_surface() -> None:
    import smcsignal
    import smcsignal.composition as composition_package

    assert composition_package.__all__ == ["execute_verified_run"]
    assert composition_package.execute_verified_run is adapter_module.execute_verified_run
    assert smcsignal.__all__ == ["__version__"]
    assert "composition" not in smcsignal.__all__


def test_importing_the_adapter_loads_nothing_beyond_its_two_upstreams() -> None:
    """In a fresh interpreter, Phase 31 adds exactly its own two modules to the graph."""

    script = (
        "import json, sys\n"
        "import smcsignal.materialization\n"
        "import smcsignal.runs\n"
        "upstream = {name for name in sys.modules if name.startswith('smcsignal')}\n"
        "import smcsignal.composition\n"
        "added = sorted(\n"
        "    name for name in sys.modules\n"
        "    if name.startswith('smcsignal') and name not in upstream\n"
        ")\n"
        "print(json.dumps(added))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert json.loads(completed.stdout) == [
        "smcsignal.composition",
        "smcsignal.composition.verified_run",
    ]


def test_importing_the_adapter_loads_no_runtime_product_or_cli_package() -> None:
    forbidden = (
        "smcsignal.delivery",
        "smcsignal.monitoring",
        "smcsignal.cli",
        "smcsignal.__main__",
    )
    script = (
        "import json, sys\n"
        "import smcsignal.composition\n"
        f"loaded = sorted(name for name in sys.modules if name.startswith({forbidden!r}))\n"
        "print(json.dumps(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert json.loads(completed.stdout) == []


# --------------------------------------------------------------------------
# Dependency direction: materialization → composition → runs, one way
# --------------------------------------------------------------------------


def test_nothing_outside_composition_imports_composition() -> None:
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.is_relative_to(COMPOSITION_ROOT):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            assert not module.startswith("smcsignal.composition"), (
                f"{path.relative_to(SRC_ROOT)} imports composition: the adapter is consumed "
                "only by callers"
            )
        assert "execute_verified_run" not in path.read_text(encoding="utf-8"), path


def test_upstreams_and_frozen_leaves_remain_unaware_of_the_adapter() -> None:
    for package in FROZEN_PACKAGES:
        for path in sorted((SRC_ROOT / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "smcsignal.composition" not in source, f"{package}/{path.name}"
            assert "execute_verified_run" not in source, f"{package}/{path.name}"
    for name in ("cli.py", "__init__.py", "__main__.py"):
        source = (SRC_ROOT / name).read_text(encoding="utf-8")
        assert "composition" not in source and "execute_verified_run" not in source, name


def test_dependency_direction_is_one_way_through_the_import_graph() -> None:
    """composition → {materialization, runs}; neither reaches back; no cycle exists."""

    edges: dict[str, set[str]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        package = path.relative_to(SRC_ROOT).parts[0]
        if package.endswith(".py"):
            package = package[:-3]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imports(tree):
            if module.startswith("smcsignal.") and module.count(".") >= 1:
                edges.setdefault(package, set()).add(module.split(".")[1])
    assert {"materialization", "runs"} <= edges["composition"]
    assert edges["composition"] <= {
        "analysis",
        "materialization",
        "persistence",
        "runs",
        "sessions",
    }

    def reaches(start: str, target: str) -> bool:
        seen: set[str] = set()
        frontier = [start]
        while frontier:
            current = frontier.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(edges.get(current, ()))
        return False

    for package in UPSTREAM_PACKAGES + FROZEN_PACKAGES:
        assert not reaches(package, "composition"), f"{package} reaches composition"
    assert reaches("composition", "runs") and reaches("composition", "materialization")
    assert not reaches("materialization", "runs")  # Phase 30 still never touches the seam
    assert not reaches("runs", "materialization")  # and 26H never learns about Phase 30
