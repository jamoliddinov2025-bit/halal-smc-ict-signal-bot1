"""Phase 1-21 preservation, scope, and Phase 22 import-graph isolation tests."""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import smcsignal
import smcsignal.analysis
from smcsignal.analysis.intelligence import analyze_report
from tests.intelligence.helpers import intelligence, report

SRC = Path(smcsignal.__file__).resolve().parent
INTELLIGENCE = SRC / "analysis" / "intelligence"
IMPROVEMENT = SRC / "analysis" / "improvement"
BASELINE = "141b2a5bfdb933b17c4d7104a3eab9fad70b4522"


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_facade_exports_the_intelligence_api_and_preserves_earlier_api() -> None:
    for name in (
        "analyze_report",
        "run_intelligence",
        "render_intelligence_text",
        "machine_intelligence_summary",
        "load_intelligence_config",
        "IntelligenceConfig",
        "IntelligenceReport",
        "IntelligenceCell",
        "IntelligencePattern",
        "DiagnosticLabel",
        "IntelligenceDimension",
    ):
        assert name in smcsignal.analysis.__all__
        assert hasattr(smcsignal.analysis, name)
    # Phase 20 and Phase 21 facade surfaces stay intact and unchanged.
    for name in ("run_backtest", "replay_history", "render_backtest_text", "machine_summary"):
        assert name in smcsignal.analysis.__all__
    for name in (
        "run_robustness",
        "evaluate_dataset",
        "render_robustness_text",
        "machine_robustness_summary",
        "RobustnessReport",
        "MarketRegime",
    ):
        assert name in smcsignal.analysis.__all__


def test_machine_intelligence_summary_is_the_intelligence_machine_summary() -> None:
    from smcsignal.analysis.intelligence.evidence import machine_summary as intelligence_bytes

    assert inspect.signature(smcsignal.analysis.machine_intelligence_summary) == inspect.signature(
        intelligence_bytes
    )


def test_no_earlier_phase_module_imports_intelligence() -> None:
    # Intelligence is a terminal consumer: nothing in Phases 1-21 may reach it.
    # The analysis facade re-exports the Phase 22 API additively and the
    # informational CLI names the current phase; no other earlier file may
    # mention intelligence at all.
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if INTELLIGENCE in path.parents:
            continue
        # Phase 23 (improvement) is a later terminal consumer that legitimately
        # consumes the Phase 22 strategy-intelligence report through its API.
        if IMPROVEMENT in path.parents:
            continue
        if "intelligence" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(SRC)))
    # The facade re-exports the Phase 22 API and the informational CLI names
    # the current phase; no other earlier file may mention intelligence.
    assert sorted(offenders) == ["analysis/__init__.py", "cli.py"]


def test_intelligence_uses_only_stdlib_and_smcsignal() -> None:
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for path in INTELLIGENCE.glob("*.py"):
        unexpected = module_imports(path) - allowed
        assert unexpected == set(), f"{path.name}: {unexpected}"


def test_intelligence_never_imports_network_execution_or_telegram_modules() -> None:
    for path in INTELLIGENCE.glob("*.py"):
        names = module_imports(path)
        assert not names & {
            "socket",
            "http",
            "requests",
            "urllib",
            "subprocess",
            "asyncio",
            "telegram",
            "ccxt",
        }


def test_intelligence_sources_contain_no_optimization_surface() -> None:
    forbidden = (
        "sklearn",
        "scipy.optimize",
        "grid_search",
        "hyperparam",
        "autotune",
        "live_trade",
        "place_order",
        "api_key",
        "webhook",
    )
    for path in INTELLIGENCE.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert word not in text, f"{path.name} mentions {word}"


def test_intelligence_only_consumes_report_models_not_phase21_calculation() -> None:
    # The intelligence layer may import Phase 21 *models* for the report types
    # but never Phase 21 calculation/regime detection (no re-detection).
    for path in INTELLIGENCE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("smcsignal.analysis.robustness"):
                    assert "models" in node.module or node.module in {
                        "smcsignal.analysis.robustness",
                    }
                if "regime" in node.module:
                    raise AssertionError(f"{path.name} must not import regime re-detection")
            if isinstance(node, ast.Import) and "regime" in " ".join(
                alias.name for alias in node.names
            ):
                raise AssertionError(f"{path.name} must not import regime re-detection")


def test_package_version_matches_the_phase22_finalization() -> None:
    assert smcsignal.__version__ == "0.22.0"


def test_pyproject_declares_no_runtime_dependencies() -> None:
    from tomllib import loads

    project = Path(SRC).parent.parent / "pyproject.toml"
    table = loads(project.read_text(encoding="utf-8"))["project"]
    assert table.get("dependencies") in ([], None)


def test_phase22_sources_are_the_only_src_changes_from_baseline() -> None:
    """Only the Phase 22 package, its additive facade exports, the two
    informational finalization files, and the downstream packages introduced by
    the later approved phases may differ from the frozen Phase 21 baseline.

    The allow-list is exact-path: any file outside it that differs from the
    Phase 21 baseline (including a new file inside an allowed package) still
    fails this guard. Extend it only when an approved phase adds sources, as was
    done here for Phase 23 (improvement) and Phase 24 (delivery/transport/
    telegram).
    """

    import subprocess

    root = Path(SRC).parent.parent
    changed = subprocess.run(
        ["git", "diff", "--name-only", BASELINE, "--", "src"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    ).stdout.split()
    allowed = {
        "src/smcsignal/__init__.py",
        "src/smcsignal/cli.py",
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
        "src/smcsignal/analysis/__init__.py",
    }
    assert set(changed) <= allowed, set(changed) - allowed


def test_report_id_is_deterministic_across_repeated_runs() -> None:
    source = report()
    first = analyze_report(source, intelligence())
    second = analyze_report(source, intelligence())
    assert first == second
    assert first.report_id == second.report_id
