"""Phase 23B scope audit: isolation, anti-optimization, no trading/telegram."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import smcsignal
from smcsignal.analysis.improvement import (
    __all__ as improvement_public,
)
from smcsignal.analysis.improvement import (
    build_candidate,
    config_hash_of,
    propose_delta,
)
from smcsignal.analysis.improvement.evaluation import apply_candidate_deltas, evaluate_candidate
from smcsignal.analysis.robustness import run_robustness
from tests.improvement.helpers import baseline_config, dataset, protocol

SRC = Path(smcsignal.__file__).resolve().parent
IMPROVEMENT = SRC / "analysis" / "improvement"

FORBIDDEN_TOKENS = (
    "sklearn",
    "scipy",
    "optuna",
    "grid_search",
    "random_search",
    "bayesian",
    "autotune",
    "hyperparam",
    "api_key",
    "place_order",
    "webhook",
    "ccxt",
    "requests",
    "aiohttp",
    "telegram",
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


def test_improvement_imports_only_stdlib_and_smcsignal() -> None:
    allowed = set(sys.stdlib_module_names) | {"smcsignal"}
    for path in IMPROVEMENT.glob("*.py"):
        imports = _module_imports(path)
        unexpected: set[str] = set()
        for dotted in imports:
            top = dotted.split(".")[0]
            if top not in allowed:
                unexpected.add(dotted)
        assert unexpected == set(), f"{path.name}: {unexpected}"


def test_improvement_never_imports_network_trading_or_telegram() -> None:
    banned = {"socket", "http", "requests", "urllib", "subprocess", "asyncio", "telegram", "ccxt"}
    for path in IMPROVEMENT.glob("*.py"):
        imports = _module_imports(path)
        tops = {item.split(".")[0] for item in imports}
        assert not tops & banned, f"{path.name}: {tops & banned}"


def test_improvement_contains_no_optimization_or_trading_surface() -> None:
    for path in IMPROVEMENT.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_TOKENS:
            assert token not in text, f"{path.name} mentions {token}"


def test_no_earlier_phase_module_imports_improvement() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if IMPROVEMENT in path.parents:
            continue
        for dotted in _module_imports(path):
            if dotted == "smcsignal.analysis.improvement" or dotted.startswith(
                "smcsignal.analysis.improvement."
            ):
                offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_no_top_level_facade_growth() -> None:
    assert not hasattr(smcsignal, "improvement")
    assert "improvement" not in smcsignal.__all__


def test_no_automatic_approval_or_promotion_surface() -> None:
    for banned in ("promote", "activate", "release", "deploy", "apply_to_strategy", "select_best"):
        assert not any(banned in name.lower() for name in improvement_public)


def test_version_unchanged_baseline() -> None:
    assert smcsignal.__version__ == "0.22.0"


def test_hypothesis_generation_cannot_reach_validation_engines() -> None:
    # Hypothesis generation and finding derivation must consume only published
    # evidence; they must never import the evaluation/comparison engines or the
    # Phase 20/21/22 run layers (which would allow reading validation outcomes).
    for path in (IMPROVEMENT / "hypotheses.py", IMPROVEMENT / "findings.py"):
        imports = _module_imports(path)
        forbidden = {
            "smcsignal.analysis.evaluation",
            "smcsignal.analysis.comparison",
            "smcsignal.analysis.backtest",
            "smcsignal.analysis.robustness",
            "smcsignal.analysis.intelligence",
        }
        assert not imports & forbidden, f"{path.name} reaches {imports & forbidden}"


def test_evaluation_reuses_unchanged_walk_forward_engine() -> None:
    baseline = baseline_config()
    candidate = build_candidate(
        baseline_hash=config_hash_of(baseline),
        deltas=(propose_delta("displacement", "atr_period", 20),),
        rationale="r",
        expected_effect="e",
    )
    evidence = evaluate_candidate(baseline, candidate, [dataset()], protocol())
    candidate_config = apply_candidate_deltas(baseline, candidate.deltas)
    direct = run_robustness([dataset()], candidate_config, protocol().robustness)
    assert evidence.provenance.robustness_report_id == direct.report_id
