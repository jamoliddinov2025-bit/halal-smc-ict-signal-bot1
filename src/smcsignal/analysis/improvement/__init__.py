"""Phase 23 public API: controlled, offline self-improvement research.

Phase 23A ships the foundational, proposal-only layer: strict improvement
configuration, an immutable Hypothesis and CandidateExperimentDef (frozen
baseline + explicit allow-listed deltas), the candidate/hypothesis status
enums, an explicit human-decision record, deterministic identities, and the
approval state machine.

Phase 23B adds the offline candidate evaluation and deterministic
baseline-versus-candidate evidence comparison layer. It consumes only the
public Phase 20/21/22 engines (never modifying them), copies only engine
metrics (never inventing one), and produces exact-Decimal differences. It
never optimizes, selects, promotes, approves, or recommends. APPROVED means
approved for future consideration only; only an explicit human decision may
produce it.
"""

from smcsignal.analysis.improvement.candidates import (
    ExperimentDeclaration,
    create_candidate_from_confirmed_hypothesis,
    validate_declaration,
)
from smcsignal.analysis.improvement.comparison import compare
from smcsignal.analysis.improvement.config import (
    DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON,
    METHODOLOGY_VERSION,
    ImprovementConfig,
    load_improvement_config,
)
from smcsignal.analysis.improvement.evaluation import (
    METHODOLOGY_VERSION as EVALUATION_METHODOLOGY_VERSION,
)
from smcsignal.analysis.improvement.evaluation import (
    EvaluationProtocol,
    apply_candidate_deltas,
    baseline_subject_id,
    config_hash_of,
    evaluate_candidate,
    evaluate_config,
    finish_experiment,
    start_experiment,
)
from smcsignal.analysis.improvement.findings import (
    EvidenceQuality,
    FindingKind,
    ResearchFinding,
    classify_evidence,
    derive_findings,
    finding_from_cell,
    finding_identity,
)
from smcsignal.analysis.improvement.hypotheses import (
    ConfirmationDecision,
    GeneratorConfig,
    HypothesisConfirmation,
    HypothesisPattern,
    confirm_hypothesis,
    confirmation_identity,
    generate_hypotheses,
)
from smcsignal.analysis.improvement.models import (
    CandidateDelta,
    CandidateExperimentDef,
    CandidateStatus,
    HumanDecisionRecord,
    Hypothesis,
    HypothesisStatus,
    build_candidate,
    build_decision,
    build_hypothesis,
    candidate_identity,
    decision_identity,
    hypothesis_identity,
    propose_delta,
)
from smcsignal.analysis.improvement.reporting import human_comparison, machine_comparison
from smcsignal.analysis.improvement.results import (
    CandidateComparisonReport,
    CandidateEvidence,
    ComparisonRow,
    EngineProvenance,
    EvaluationStatus,
    FlatMetric,
    comparison_identity,
)
from smcsignal.analysis.improvement.review import (
    ResearchReviewReport,
    build_review_report,
    review_identity,
    review_json,
    review_text,
)
from smcsignal.analysis.improvement.state import apply_human_decision, transition
from smcsignal.analysis.improvement.surfaces import (
    ALLOWED_SURFACES,
    ChangeKind,
    ChangeSurface,
    allowed_keys,
    lookup_surface,
    validate_new_value,
)

__all__ = [
    "ALLOWED_SURFACES",
    "ChangeKind",
    "ChangeSurface",
    "ConfirmationDecision",
    "DEFAULT_MINIMUM_FINALIZED_FOR_COMPARISON",
    "EVALUATION_METHODOLOGY_VERSION",
    "METHODOLOGY_VERSION",
    "CandidateComparisonReport",
    "CandidateDelta",
    "CandidateEvidence",
    "CandidateExperimentDef",
    "CandidateStatus",
    "ComparisonRow",
    "EngineProvenance",
    "EvaluationProtocol",
    "EvaluationStatus",
    "EvidenceQuality",
    "ExperimentDeclaration",
    "FindingKind",
    "FlatMetric",
    "GeneratorConfig",
    "HumanDecisionRecord",
    "Hypothesis",
    "HypothesisConfirmation",
    "HypothesisPattern",
    "HypothesisStatus",
    "ImprovementConfig",
    "ResearchFinding",
    "ResearchReviewReport",
    "allowed_keys",
    "apply_candidate_deltas",
    "apply_human_decision",
    "baseline_subject_id",
    "build_candidate",
    "build_decision",
    "build_hypothesis",
    "build_review_report",
    "candidate_identity",
    "classify_evidence",
    "compare",
    "comparison_identity",
    "config_hash_of",
    "confirmation_identity",
    "confirm_hypothesis",
    "create_candidate_from_confirmed_hypothesis",
    "decision_identity",
    "derive_findings",
    "evaluate_candidate",
    "evaluate_config",
    "finding_from_cell",
    "finding_identity",
    "finish_experiment",
    "generate_hypotheses",
    "human_comparison",
    "review_identity",
    "review_json",
    "review_text",
    "start_experiment",
    "validate_declaration",
    "hypothesis_identity",
    "load_improvement_config",
    "lookup_surface",
    "machine_comparison",
    "propose_delta",
    "transition",
    "validate_new_value",
]
