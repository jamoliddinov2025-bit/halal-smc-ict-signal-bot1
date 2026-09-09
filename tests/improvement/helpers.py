"""Shared Phase 23B fixtures: a frozen permissive baseline plus one dataset.

The baseline keeps the allow-listed displacement ATR at 14 so a candidate
delta on it is valid, and keeps the non-delta publish thresholds low so the
small synthetic dataset still publishes enough finalized validation rows for
the unchanged Phase 20/21/22 engines to run.
"""

from __future__ import annotations

from dataclasses import replace

from smcsignal.analysis.backtest import BacktestConfiguration, ReplayDataset
from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.improvement.evaluation import (
    EvaluationProtocol,
    baseline_subject_id,
    config_hash_of,
    evaluate_config,
)
from smcsignal.analysis.improvement.results import CandidateEvidence
from smcsignal.analysis.intelligence import IntelligenceConfig
from smcsignal.analysis.robustness import RobustnessConfig
from tests.backtest.helpers import configuration
from tests.backtest.helpers import dataset as build_dataset
from tests.robustness.helpers import RISE_THEN_CHOP, robustness

DATASET_ID = "phase23b-fixture:v1"


def baseline_config() -> BacktestConfiguration:
    """A permissive frozen baseline whose ATR setting equals its allow-list value."""
    return replace(configuration(), displacement=DisplacementConfig(atr_period=14))


def dataset() -> ReplayDataset:
    return build_dataset(prices=RISE_THEN_CHOP, dataset_id=DATASET_ID)


def robustness_config() -> RobustnessConfig:
    return robustness()


def protocol(minimum_finalized_for_comparison: int = 30) -> EvaluationProtocol:
    return EvaluationProtocol(
        name="phase23b-fixture",
        dataset_ids=(DATASET_ID,),
        robustness=robustness_config(),
        intelligence=IntelligenceConfig(),
        minimum_finalized_for_comparison=minimum_finalized_for_comparison,
    )


def baseline_evidence() -> CandidateEvidence:
    """Evaluate the frozen baseline through the full unchanged harness."""
    baseline = baseline_config()
    hashed = config_hash_of(baseline)
    return evaluate_config(
        [dataset()],
        baseline,
        protocol(),
        subject=baseline_subject_id(hashed),
        baseline_hash=hashed,
    )
