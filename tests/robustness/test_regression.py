"""Phase 1-20 preservation and Phase 21 import-graph isolation tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import smcsignal
import smcsignal.analysis
from smcsignal.analysis.backtest import run_backtest
from tests.robustness.helpers import configuration, dataset

SRC = Path(smcsignal.__file__).resolve().parent
ROBUSTNESS = SRC / "analysis" / "robustness"
INTELLIGENCE = SRC / "analysis" / "intelligence"
IMPROVEMENT = SRC / "analysis" / "improvement"
BACKTEST = SRC / "analysis" / "backtest"


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_no_earlier_phase_module_imports_robustness() -> None:
    # Robustness is a terminal consumer: nothing in Phases 1-20 may reach it.
    # The analysis facade re-exports the Phase 21 API additively. Phase 22
    # (intelligence) is a later terminal consumer that legitimately reads the
    # robustness report, and the Phase 22 CLI no longer names robustness; no
    # other earlier file may mention robustness at all.
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if ROBUSTNESS in path.parents:
            continue
        if INTELLIGENCE in path.parents:
            continue
        # Phase 23 (improvement) is a later terminal consumer that legitimately
        # reads the Phase 21 robustness report through its public API.
        if IMPROVEMENT in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        if "robustness" in source:
            offenders.append(str(path.relative_to(SRC)))
    assert sorted(offenders) == ["analysis/__init__.py"]


def test_backtest_sources_never_reference_robustness() -> None:
    for path in BACKTEST.glob("*.py"):
        assert "robustness" not in path.read_text(encoding="utf-8")


def test_robustness_uses_only_stdlib_and_smcsignal() -> None:
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for path in ROBUSTNESS.glob("*.py"):
        unexpected = module_imports(path) - allowed
        assert unexpected == set(), f"{path.name}: {unexpected}"


def test_robustness_never_imports_network_or_execution_modules() -> None:
    for path in ROBUSTNESS.glob("*.py"):
        names = module_imports(path)
        assert not names & {"socket", "http", "requests", "urllib", "subprocess", "asyncio"}


def test_facade_exports_both_backtest_and_robustness_api() -> None:
    for name in (
        "run_robustness",
        "evaluate_dataset",
        "render_robustness_text",
        "machine_robustness_summary",
        "load_robustness_config",
        "RobustnessConfig",
        "RobustnessReport",
        "RegimeAnalyzer",
        "MarketRegime",
        "SegmentStatus",
        "RegimeObservation",
        "DatasetRobustnessResult",
        "analyze_regimes",
    ):
        assert name in smcsignal.analysis.__all__
        assert hasattr(smcsignal.analysis, name)
    # Phase 20 facade surface stays intact
    for name in ("run_backtest", "replay_history", "render_backtest_text", "machine_summary"):
        assert name in smcsignal.analysis.__all__


def test_facade_backtest_machine_summary_is_unchanged() -> None:
    # The Phase 20 machine_summary export must still accept a backtest report.
    import inspect

    from smcsignal.analysis.backtest import machine_summary

    assert inspect.signature(smcsignal.analysis.machine_summary) == inspect.signature(
        machine_summary
    )


def test_phase20_backtest_id_is_byte_identical_to_the_baseline() -> None:
    result = run_backtest([dataset()], configuration())
    assert (
        result.backtest_id
        == "backtest:fdede9aebe06581a609e3c990b81fe0559bf44610798d708bfe0a575bad7638b"
    )


def test_phase20_buy_pattern_is_unchanged() -> None:
    from smcsignal.analysis.backtest.calculation import replay_rows

    result = run_backtest([dataset()], configuration())
    rows = replay_rows(result.replays[0])
    assert [row.candle_index for row in rows] == [4, 8, 12, 16]
    assert [row.score_total for row in rows] == [25, 25, 25, 25]
    assert all(row.direction.value == "LONG" for row in rows)


def test_package_version_matches_the_phase22_finalization() -> None:
    assert smcsignal.__version__ == "0.22.0"


def test_pyproject_declares_no_runtime_dependencies() -> None:
    from tomllib import loads

    project = Path(SRC).parent.parent / "pyproject.toml"
    table = loads(project.read_text(encoding="utf-8"))["project"]
    assert table.get("dependencies") in ([], None)


def test_decision_modules_are_unmodified_since_the_phase20_baseline() -> None:
    """Only the Phase 21 package, its additive facade exports, the two
    informational finalization files (package version, CLI phase text), and the
    downstream packages introduced by the later approved phases may differ from
    the frozen Phase 20 baseline; every decision module is untouched.

    The allow-list is exact-path, not prefix-based: any file outside it that
    differs from the Phase 20 baseline (including a new file inside an allowed
    package) still fails this guard. Extend it only when an approved phase adds
    sources, as was done here for Phase 23 (improvement) and Phase 24
    (delivery/transport/telegram).
    """

    import subprocess

    root = Path(SRC).parent.parent
    changed = subprocess.run(
        ["git", "diff", "--name-only", "cfe51fbe9212434f97ae648b3f50d3bd223aeec9", "--", "src"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    ).stdout.split()
    allowed = {
        "src/smcsignal/__init__.py",
        "src/smcsignal/cli.py",
        "src/smcsignal/analysis/robustness/__init__.py",
        "src/smcsignal/analysis/robustness/analyzer.py",
        "src/smcsignal/analysis/robustness/calculation.py",
        "src/smcsignal/analysis/robustness/config.py",
        "src/smcsignal/analysis/robustness/evidence.py",
        "src/smcsignal/analysis/robustness/models.py",
        "src/smcsignal/analysis/robustness/regime.py",
        "src/smcsignal/analysis/robustness/text.py",
        "src/smcsignal/analysis/intelligence/__init__.py",
        "src/smcsignal/analysis/intelligence/analyzer.py",
        "src/smcsignal/analysis/intelligence/calculation.py",
        "src/smcsignal/analysis/intelligence/config.py",
        "src/smcsignal/analysis/intelligence/evidence.py",
        "src/smcsignal/analysis/intelligence/models.py",
        "src/smcsignal/analysis/intelligence/text.py",
        # Phase 23 approved the improvement research package: a terminal
        # consumer of Phase 22 reports that publishes nothing and cannot reach
        # any decision module.
        "src/smcsignal/analysis/improvement/__init__.py",
        "src/smcsignal/analysis/improvement/candidates.py",
        "src/smcsignal/analysis/improvement/comparison.py",
        "src/smcsignal/analysis/improvement/config.py",
        "src/smcsignal/analysis/improvement/evaluation.py",
        "src/smcsignal/analysis/improvement/evidence.py",
        "src/smcsignal/analysis/improvement/findings.py",
        "src/smcsignal/analysis/improvement/hypotheses.py",
        "src/smcsignal/analysis/improvement/models.py",
        "src/smcsignal/analysis/improvement/reporting.py",
        "src/smcsignal/analysis/improvement/results.py",
        "src/smcsignal/analysis/improvement/review.py",
        "src/smcsignal/analysis/improvement/state.py",
        "src/smcsignal/analysis/improvement/surfaces.py",
        # Phase 24 approved the delivery core (24B presentation/transport
        # payload, 24C orchestration) and the Telegram transport (24C/24D).
        # Delivery is output-only and strictly downstream: it consumes already
        # published, already rendered signals and can never generate, gate,
        # veto, or rescore one.
        "src/smcsignal/delivery/__init__.py",
        "src/smcsignal/delivery/audit.py",
        "src/smcsignal/delivery/chart.py",
        "src/smcsignal/delivery/config.py",
        "src/smcsignal/delivery/formatting.py",
        "src/smcsignal/delivery/identity.py",
        "src/smcsignal/delivery/message.py",
        "src/smcsignal/delivery/models.py",
        "src/smcsignal/delivery/orchestrator.py",
        "src/smcsignal/delivery/sink.py",
        "src/smcsignal/delivery/transport.py",
        "src/smcsignal/delivery/telegram/__init__.py",
        "src/smcsignal/delivery/telegram/audit.py",
        "src/smcsignal/delivery/telegram/config.py",
        "src/smcsignal/delivery/telegram/destination.py",
        "src/smcsignal/delivery/telegram/http.py",
        "src/smcsignal/delivery/telegram/integration.py",
        "src/smcsignal/delivery/telegram/rate_limit.py",
        "src/smcsignal/delivery/telegram/sink.py",
        # Phase 25 approved a read-only, strictly downstream monitoring layer.
        # It observes already published immutable records and returns monitoring
        # values only: it cannot generate, gate, veto, reinterpret, or modify a
        # signal, a classification, a decision, or a delivery. Phase 25B-1 adds
        # the foundations (errors, models, clocks, configuration) and Phase 25B-2
        # adds health transitions, precedence rollup, and the metric model layer.
        "src/smcsignal/monitoring/__init__.py",
        "src/smcsignal/monitoring/clock.py",
        "src/smcsignal/monitoring/config.py",
        "src/smcsignal/monitoring/errors.py",
        "src/smcsignal/monitoring/health.py",
        "src/smcsignal/monitoring/metrics.py",
        "src/smcsignal/monitoring/models.py",
        "src/smcsignal/monitoring/monitor.py",
        "src/smcsignal/monitoring/observers/__init__.py",
        "src/smcsignal/monitoring/observers/data.py",
        "src/smcsignal/monitoring/report.py",
        "src/smcsignal/monitoring/serialization.py",
        "src/smcsignal/monitoring/session.py",
        # Phase 26A approved a strictly downstream analytics connection. It
        # observes immutable SignalSnapshot frames exactly where the Phase 17
        # engine publishes them, opens one OPEN Phase 18 outcome per published
        # BUY via the existing open_outcome() mapping, and accepts finalization
        # only from the real market outcome evaluator. It imports nothing from
        # delivery or monitoring, nothing upstream imports it, and it can never
        # generate, gate, veto, reinterpret, or modify a signal, a halal
        # classification, an eligibility decision, or a delivery. A delivery
        # state is never a trade outcome.
        "src/smcsignal/analytics/__init__.py",
        "src/smcsignal/analytics/models.py",
        "src/smcsignal/analytics/observer.py",
        "src/smcsignal/analysis/__init__.py",
    }
    assert set(changed) <= allowed, set(changed) - allowed


def test_no_signal_or_outcome_semantics_changed_in_robustness_sources() -> None:
    # The robustness layer may read published records but never publishes,
    # vetoes, or scores: no analyzer/state imports outside the replay layer.
    for path in ROBUSTNESS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "signal_engine" not in node.module
                assert "outcome_tracking.analyzer" not in node.module
                assert "liquidity.analyzer" not in node.module
