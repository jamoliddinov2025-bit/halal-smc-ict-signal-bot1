"""Phase 20 regression: isolation, scope, dependencies, and pinned baselines."""

from __future__ import annotations

import ast
import sys
from decimal import Decimal
from pathlib import Path

from smcsignal.analysis.backtest import (
    BacktestConfiguration,
    BacktestReport,
    BacktestSignalResult,
    HistoricalReplay,
    ReplayDataset,
    ReplayResult,
    ReplayStep,
    load_backtest_config,
    load_backtest_configuration,
    machine_summary,
    render_backtest_text,
    replay_history,
    run_backtest,
)
from smcsignal.analysis.outcome_tracking import OutcomeStatus
from tests.backtest.helpers import configuration, dataset, report

SRC = Path(__file__).resolve().parents[2] / "src" / "smcsignal"
BACKTEST = SRC / "analysis" / "backtest"
PHASE_1_19_SOURCES = (
    [
        path
        for path in sorted((SRC / "analysis").rglob("*.py"))
        if "backtest" not in path.relative_to(SRC).parts
        and "robustness" not in path.relative_to(SRC).parts
        and "intelligence" not in path.relative_to(SRC).parts
    ]
    + sorted(SRC.glob("*.py"))
    + sorted((SRC / "data").rglob("*.py"))
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_no_phase_1_19_module_imports_the_backtest_layer() -> None:
    consumers = [
        path
        for path in PHASE_1_19_SOURCES
        if path.name != "__init__.py" or path.parent != SRC / "analysis"
    ]
    offenders = [
        str(path.relative_to(SRC))
        for path in consumers
        if any(
            module.startswith("smcsignal.analysis.backtest") for module in _imported_modules(path)
        )
    ]
    assert offenders == []


def test_the_analysis_facade_exports_the_backtest_api() -> None:
    import smcsignal.analysis as package

    for name in (
        "BacktestConfig",
        "BacktestConfiguration",
        "BacktestReport",
        "BacktestSignalResult",
        "HistoricalReplay",
        "ReplayDataset",
        "ReplayResult",
        "ReplayStep",
        "load_backtest_config",
        "load_backtest_configuration",
        "machine_summary",
        "replay_history",
        "render_backtest_text",
        "run_backtest",
    ):
        assert name in package.__all__
        assert getattr(package, name) is not None


def test_backtest_modules_import_only_existing_packages_and_stdlib() -> None:
    allowed_roots = ("smcsignal", "datetime", "decimal", "typing", "dataclasses", "collections")
    allowed_exact = {"hashlib", "json", "tomllib", "pathlib", "enum", "types", "__future__"}
    for path in sorted(BACKTEST.glob("*.py")):
        for module in _imported_modules(path):
            assert module.split(".")[0] in allowed_roots or module in allowed_exact, (
                f"{path.name} imports {module}"
            )


def test_backtest_sources_contain_no_network_trading_or_secret_surface() -> None:
    forbidden = (
        "telegram",
        "api_key",
        "apikey",
        "secret",
        "token",
        "ccxt",
        "webhook",
        "requests",
        "urllib",
        "socket",
        "place_order",
        "create_order",
        "leverage",
        "margin",
        "optimize",
        "password",
    )
    for path in sorted(BACKTEST.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert word not in text, f"{path.name} mentions {word}"


def test_backtest_adds_no_runtime_dependency_entries() -> None:
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    text = project.read_text(encoding="utf-8")
    dependencies = ast.literal_eval(text.split("dependencies = ")[1].split("\n")[0].strip())
    assert dependencies == []


def test_package_version_matches_the_current_release() -> None:
    import smcsignal

    # Bumped to 0.22.0 by the approved Phase 22 finalization.
    assert smcsignal.__version__ == "0.22.0"


def test_baseline_signal_behavior_matches_phase_18_and_19() -> None:
    result = report()
    assert [row.candle_index for row in result.signals] == [4, 8, 12, 16]
    assert [row.score_total for row in result.signals] == [25, 25, 25, 25]
    assert [row.outcome_status for row in result.signals] == [
        OutcomeStatus.WIN,
        OutcomeStatus.OPEN,
        OutcomeStatus.OPEN,
        OutcomeStatus.OPEN,
    ]
    overall = result.performance.overall
    assert overall.win_count == 1
    assert overall.win_rate == Decimal(1)
    assert overall.final_return_sum == Decimal(
        "0.41666666666666666666666666666666666666666666666667"
    )


def test_replays_do_not_change_existing_decision_facts() -> None:
    from tests.outcome_tracking.helpers import signal_frames

    before = signal_frames()
    config = configuration()
    for _ in range(2):
        run_backtest([dataset()], config)
    assert signal_frames() == before


def test_backtest_reports_are_reproducible_across_processes_of_the_same_interpreter() -> None:
    first = report()
    second = report()
    assert first == second
    assert machine_summary(first) == machine_summary(second)
    assert first.backtest_id == second.backtest_id
    assert first.replays[0].replay_id == second.replays[0].replay_id


def test_public_api_surface_is_importable() -> None:
    assert all(
        callable(item) or isinstance(item, type)
        for item in (
            HistoricalReplay,
            replay_history,
            run_backtest,
            render_backtest_text,
            machine_summary,
            load_backtest_config,
            load_backtest_configuration,
            BacktestConfiguration,
            ReplayDataset,
            ReplayResult,
            ReplayStep,
            BacktestReport,
            BacktestSignalResult,
        )
    )


def test_no_new_test_dependencies_beyond_the_existing_suite() -> None:
    helpers = Path(__file__).resolve().parent / "helpers.py"
    tree = ast.parse(helpers.read_text(encoding="utf-8"))
    allowed = {"smcsignal", "datetime", "decimal"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            assert node.module.split(".")[0] in allowed


def test_sys_modules_contains_no_network_or_exchange_clients() -> None:
    suspicious = [
        name for name in sys.modules if name.split(".")[0] in ("requests", "ccxt", "websocket")
    ]
    assert suspicious == []
