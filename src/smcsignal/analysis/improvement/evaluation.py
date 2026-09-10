"""Phase 23B offline candidate evaluation over the existing Phase 20/21/22 engines.

A candidate is evaluated by (1) building an isolated candidate configuration
that is the frozen baseline with only the candidate's explicit, allow-listed
deltas applied, (2) replaying/backtesting it with the unchanged Phase 20
engine, (3) running the unchanged Phase 21 walk-forward robustness engine over
the same datasets/protocol, and (4) running the unchanged Phase 22 strategy
intelligence engine over that robustness report. The frozen baseline is then
run through the very same harness so a deterministic candidate-versus-baseline
comparison can be produced. No Phase 20/21/22 engine is modified, no metric is
invented, and nothing here optimizes, selects, promotes, or approves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields, replace
from decimal import Decimal

from smcsignal.analysis.backtest import (
    METHODOLOGY_VERSION as BACKTEST_VERSION,
)
from smcsignal.analysis.backtest import (
    BacktestConfiguration,
    ReplayDataset,
    configuration_hash,
    run_backtest,
)
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.evidence import compose_id
from smcsignal.analysis.improvement.models import (
    CandidateDelta,
    CandidateExperimentDef,
    CandidateStatus,
)
from smcsignal.analysis.improvement.results import (
    CandidateEvidence,
    EngineProvenance,
    EvaluationStatus,
    FlatMetric,
)
from smcsignal.analysis.improvement.state import transition
from smcsignal.analysis.intelligence import (
    METHODOLOGY_VERSION as INTELLIGENCE_VERSION,
)
from smcsignal.analysis.intelligence import IntelligenceConfig, run_intelligence
from smcsignal.analysis.intelligence.models import IntelligenceReport
from smcsignal.analysis.robustness import (
    METHODOLOGY_VERSION as ROBUSTNESS_VERSION,
)
from smcsignal.analysis.robustness import RobustnessConfig, run_robustness
from smcsignal.analysis.robustness.models import (
    DegradationSummary,
    StabilitySummary,
)

METHODOLOGY_VERSION = "improvement-eval-v1"

# Metric attributes present verbatim on both the Phase 19 PerformanceBucket and
# the Phase 22 IntelligenceCell; Phase 23B only copies them, it never computes
# or invents a metric. Names equal the attribute name on the source object.
_METRIC_ATTRS = (
    "total_buy_signals",
    "open_count",
    "win_count",
    "loss_count",
    "flat_count",
    "finalized_count",
    "final_return_sum",
    "mfe_return_sum",
    "mae_return_sum",
    "win_rate",
    "average_final_return",
    "average_mfe_return",
    "average_mae_return",
)


def _as_decimal(value: object) -> Decimal | None:
    """Exact Decimal copy of an engine scalar; ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AnalysisInputError("engine metric must be a finite Decimal")
        return value
    if isinstance(value, int):
        return Decimal(value)
    raise AnalysisInputError(
        "engine metrics must be exact Decimal or integer values, never rounded floats"
    )


@dataclass(frozen=True, slots=True)
class EvaluationProtocol:
    """The frozen, declared evaluation protocol shared by candidate and baseline."""

    name: str
    dataset_ids: tuple[str, ...]
    robustness: RobustnessConfig
    intelligence: IntelligenceConfig
    minimum_finalized_for_comparison: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or self.name != self.name.strip()
        ):
            raise AnalysisInputError("protocol name must be a nonempty, trimmed string")
        if not self.dataset_ids or not all(
            isinstance(k, str) and k.strip() for k in self.dataset_ids
        ):
            raise AnalysisInputError("protocol dataset_ids must be a nonempty tuple of strings")
        if not isinstance(self.robustness, RobustnessConfig):
            raise AnalysisInputError("protocol robustness must be a RobustnessConfig")
        if not isinstance(self.intelligence, IntelligenceConfig):
            raise AnalysisInputError("protocol intelligence must be an IntelligenceConfig")
        if (
            type(self.minimum_finalized_for_comparison) is not int
            or self.minimum_finalized_for_comparison < 1
        ):
            raise AnalysisInputError(
                "protocol minimum_finalized_for_comparison must be a positive integer"
            )

    def identity(self) -> str:
        """Deterministic identity of this frozen protocol (no clocks or randomness)."""
        return compose_id(
            "protocol",
            {
                "methodology": METHODOLOGY_VERSION,
                "name": self.name,
                "dataset_ids": sorted(self.dataset_ids),
                "minimum_finalized_for_comparison": self.minimum_finalized_for_comparison,
                "robustness": self.robustness,
                "intelligence": self.intelligence,
            },
        )


def apply_candidate_deltas(
    baseline: BacktestConfiguration,
    deltas: tuple[CandidateDelta, ...],
) -> BacktestConfiguration:
    """Return an isolated candidate configuration from the frozen baseline.

    Each delta is applied to its own component/setting through an immutable
    ``dataclasses.replace`` chain; the baseline configuration object is never
    mutated, so evaluating one candidate cannot contaminate another. Any
    undeclared free-form change, a mismatch between the declared ``old_value``
    and the actual frozen baseline value, or a delta whose component is not a
    Phase 20 configuration field is rejected.
    """

    if not isinstance(baseline, BacktestConfiguration):
        raise AnalysisInputError("baseline must be a BacktestConfiguration")
    if not isinstance(deltas, tuple) or not all(
        isinstance(delta, CandidateDelta) for delta in deltas
    ):
        raise AnalysisInputError("deltas must be a tuple of CandidateDelta records")
    if not deltas:
        raise AnalysisInputError("a candidate must declare at least one delta")
    field_names = {field.name for field in fields(BacktestConfiguration)}
    seen: set[str] = set()
    for delta in deltas:
        component = delta.component
        if component not in field_names:
            raise AnalysisInputError(f"{component} is not a backtest configuration field")
        if component in seen:
            raise AnalysisInputError(f"duplicate delta for component {component}")
        seen.add(component)
        component_obj = getattr(baseline, component)
        if not hasattr(component_obj, delta.setting):
            raise AnalysisInputError(
                f"{component}.{delta.setting} is not a declared configuration setting"
            )
        if getattr(component_obj, delta.setting) != delta.old_value:
            raise AnalysisInputError(
                f"{component}.{delta.setting} delta old_value must equal the frozen "
                f"baseline value {delta.old_value!r}"
            )
    config = baseline
    for delta in deltas:
        component_obj = getattr(config, delta.component)
        updated = replace(component_obj, **{delta.setting: delta.new_value})
        config = replace(config, **{delta.component: updated})
    return config


def _run_identity(
    *,
    config: BacktestConfiguration,
    dataset_ids: tuple[str, ...],
    protocol_identity: str,
) -> str:
    """Deterministic evaluation run id; independent of prose and display time."""
    return compose_id(
        "evaluation",
        {
            "methodology": METHODOLOGY_VERSION,
            "config_hash": configuration_hash(config),
            "dataset_ids": sorted(dataset_ids),
            "protocol_identity": protocol_identity,
        },
    )


def _cell_metrics(
    dimension: str, name: str, cell: object, *, sample: int
) -> tuple[FlatMetric, ...]:
    """Copy engine-provided cell/bucket metrics verbatim into FlatMetric rows."""
    rows: list[FlatMetric] = []
    for attr in _METRIC_ATTRS:
        value = _as_decimal(getattr(cell, attr))
        rows.append(
            FlatMetric(
                dimension=dimension,
                group=name,
                metric=attr,
                value=value,
                sample_finalized=sample,
            )
        )
    return tuple(rows)


def _degradation_metrics(degradation: DegradationSummary) -> tuple[FlatMetric, ...]:
    """Robustness degradation summary scalars (descriptive, not outcome-sample)."""
    return (
        FlatMetric("degradation", "all", "window_count", _as_decimal(degradation.window_count), 0),
        FlatMetric(
            "degradation",
            "all",
            "comparable_window_count",
            _as_decimal(degradation.comparable_window_count),
            0,
        ),
        FlatMetric(
            "degradation",
            "all",
            "average_win_rate_degradation",
            _as_decimal(degradation.average_win_rate_degradation),
            0,
        ),
        FlatMetric(
            "degradation",
            "all",
            "average_final_return_degradation",
            _as_decimal(degradation.average_final_return_degradation),
            0,
        ),
    )


def _stability_metrics(stability: StabilitySummary) -> tuple[FlatMetric, ...]:
    """Robustness stability summary scalars (descriptive, not outcome-sample)."""
    return (
        FlatMetric(
            "stability",
            "all",
            "validation_segment_count",
            _as_decimal(stability.validation_segment_count),
            0,
        ),
        FlatMetric(
            "stability",
            "all",
            "sufficient_segment_count",
            _as_decimal(stability.sufficient_segment_count),
            0,
        ),
        FlatMetric(
            "stability",
            "all",
            "positive_segment_count",
            _as_decimal(stability.positive_segment_count),
            0,
        ),
        FlatMetric(
            "stability",
            "all",
            "negative_segment_count",
            _as_decimal(stability.negative_segment_count),
            0,
        ),
        FlatMetric(
            "stability",
            "all",
            "win_rate_spread",
            _as_decimal(stability.win_rate_spread),
            0,
        ),
        FlatMetric(
            "stability",
            "all",
            "average_final_return_spread",
            _as_decimal(stability.average_final_return_spread),
            0,
        ),
    )


def _intelligence_metrics(intelligence_report: IntelligenceReport) -> tuple[FlatMetric, ...]:
    """Flatten intelligence overall/setup/symbol/timeframe/month/regime cells."""
    rows: list[FlatMetric] = []
    rows.extend(
        _cell_metrics(
            "overall",
            "all",
            intelligence_report.overall,
            sample=intelligence_report.overall.finalized_count,
        )
    )
    for dimension, cells in (
        ("setup", intelligence_report.by_setup),
        ("symbol", intelligence_report.by_symbol),
        ("timeframe", intelligence_report.by_timeframe),
        ("month", intelligence_report.by_month),
        ("regime", intelligence_report.by_regime),
    ):
        for cell in cells:
            rows.extend(_cell_metrics(dimension, cell.name, cell, sample=cell.finalized_count))
    return tuple(rows)


def evaluate_config(
    datasets: Iterable[ReplayDataset],
    config: BacktestConfiguration,
    protocol: EvaluationProtocol,
    *,
    subject: str,
    baseline_hash: str,
) -> CandidateEvidence:
    """Run one configuration (baseline or candidate) through the same harness.

    Runs the unchanged Phase 20/21/22 engines in their causal order and copies
    their reported metrics into an immutable :class:`CandidateEvidence`.
    ``subject`` labels what was run; ``baseline_hash`` is the frozen baseline
    configuration hash the run is anchored to.
    """

    if not isinstance(config, BacktestConfiguration):
        raise AnalysisInputError("evaluation requires a BacktestConfiguration")
    if not isinstance(protocol, EvaluationProtocol):
        raise AnalysisInputError("evaluation requires an EvaluationProtocol")
    if not isinstance(subject, str) or not subject.strip():
        raise AnalysisInputError("subject must be a nonempty string")
    try:
        materialized = tuple(datasets)
    except TypeError as exc:
        raise AnalysisInputError("datasets must be an iterable of ReplayDataset records") from exc
    if not materialized or not all(isinstance(dataset, ReplayDataset) for dataset in materialized):
        raise AnalysisInputError("datasets must be a nonempty iterable of ReplayDataset records")
    protocol_identity = protocol.identity()
    dataset_ids = tuple(sorted(dataset.dataset_id for dataset in materialized))
    if dataset_ids != tuple(sorted(protocol.dataset_ids)):
        raise AnalysisInputError(
            "evaluation datasets must match the frozen protocol dataset identity"
        )
    backtest_report = run_backtest(materialized, config)
    robustness_report = run_robustness(materialized, config, protocol.robustness)
    intelligence_report = run_intelligence(robustness_report, protocol.intelligence)

    metrics = list(_intelligence_metrics(intelligence_report))
    metrics.extend(
        _cell_metrics(
            "robustness_overall",
            "all",
            robustness_report.overall,
            sample=robustness_report.overall.finalized_count,
        )
    )
    metrics.extend(_degradation_metrics(robustness_report.degradation))
    metrics.extend(_stability_metrics(robustness_report.stability))

    provenance = EngineProvenance(
        backtest_id=backtest_report.backtest_id,
        robustness_report_id=robustness_report.report_id,
        intelligence_report_id=intelligence_report.report_id,
        backtest_version=BACKTEST_VERSION,
        robustness_version=ROBUSTNESS_VERSION,
        intelligence_version=INTELLIGENCE_VERSION,
    )
    run_id = _run_identity(
        config=config,
        dataset_ids=dataset_ids,
        protocol_identity=protocol_identity,
    )
    return CandidateEvidence(
        subject_id=subject,
        baseline_hash=baseline_hash,
        run_id=run_id,
        dataset_ids=dataset_ids,
        protocol_identity=protocol_identity,
        provenance=provenance,
        metrics=tuple(metrics),
        limitations=_limitations(protocol.minimum_finalized_for_comparison),
        status=EvaluationStatus.EVALUATED,
    )


def config_hash_of(config: BacktestConfiguration) -> str:
    """Canonical SHA-256 of a Phase 20 backtest configuration."""
    if not isinstance(config, BacktestConfiguration):
        raise AnalysisInputError("config_hash_of requires a BacktestConfiguration")
    return configuration_hash(config)


def _limitations(minimum: int) -> tuple[str, ...]:
    return (
        "historical-research-only comparison of existing engine reports; "
        "better historical numbers are evidence only and imply no adoption.",
        f"no claim is made below the {minimum}-finalized comparison minimum; "
        "raw counts are always preserved.",
        "no statistical significance is inferred from any difference.",
        "Phase 23B computes no metric that the Phase 20/21/22 engines do not "
        "already provide; all rates/averages are exact Decimal.",
        "no optimization, ranking, selection, promotion, or live trading is performed or implied.",
    )


def evaluate_candidate(
    baseline_config: BacktestConfiguration,
    candidate: CandidateExperimentDef,
    datasets: Iterable[ReplayDataset],
    protocol: EvaluationProtocol,
) -> CandidateEvidence:
    """Evaluate one explicitly declared candidate in isolation.

    Builds the isolated candidate configuration from the frozen baseline,
    verifies the candidate is anchored to this exact baseline hash, and runs
    the whole unchanged harness for the candidate configuration only.
    """

    if not isinstance(baseline_config, BacktestConfiguration):
        raise AnalysisInputError("baseline_config must be a BacktestConfiguration")
    if not isinstance(candidate, CandidateExperimentDef):
        raise AnalysisInputError("candidate must be a CandidateExperimentDef")
    baseline_hash = config_hash_of(baseline_config)
    if candidate.baseline_hash != baseline_hash:
        raise AnalysisInputError(
            "candidate baseline_hash must equal the frozen baseline configuration hash"
        )
    candidate_config = apply_candidate_deltas(baseline_config, candidate.deltas)
    return evaluate_config(
        datasets,
        candidate_config,
        protocol,
        subject=candidate.candidate_id,
        baseline_hash=baseline_hash,
    )


def baseline_subject_id(baseline_hash: str) -> str:
    """The canonical subject label of a frozen-baseline evaluation run."""
    return f"baseline:{baseline_hash}"


def start_experiment(candidate: CandidateExperimentDef) -> CandidateExperimentDef:
    """Move an explicitly declared candidate from PROPOSED to EXPERIMENTAL.

    This is the only transition the evaluator performs before running the
    unchanged engines. It uses the Phase 23A state machine, so it can never
    produce APPROVED or REJECTED.
    """
    if not isinstance(candidate, CandidateExperimentDef):
        raise AnalysisInputError("start_experiment requires a CandidateExperimentDef")
    return transition(candidate, CandidateStatus.EXPERIMENTAL)


def finish_experiment(
    candidate: CandidateExperimentDef, evidence: CandidateEvidence
) -> CandidateExperimentDef:
    """Move an EXPERIMENTAL candidate to EVALUATED (evidence) or FAILED.

    Delegates to the Phase 23A state machine's automatic transitions, so a
    FAILED experiment can never reach approval and APPROVED/REJECTED are never
    produced here; those require the explicit human decision operation.
    """
    if not isinstance(candidate, CandidateExperimentDef):
        raise AnalysisInputError("finish_experiment requires a CandidateExperimentDef")
    if not isinstance(evidence, CandidateEvidence):
        raise AnalysisInputError("finish_experiment requires CandidateEvidence")
    if evidence.status is EvaluationStatus.FAILED:
        return transition(candidate, CandidateStatus.FAILED)
    return transition(candidate, CandidateStatus.EVALUATED)
