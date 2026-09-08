"""Immutable eligibility facts, decisions, and published snapshots. Not orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter.classification import AssetClassification
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference
from smcsignal.analysis.setup_quality.models import ScoreSnapshot
from smcsignal.analysis.setup_quality.models import current_observation as halal_observation
from smcsignal.analysis.signal_eligibility.config import SignalEligibilityConfig


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class MarketBias(StrEnum):
    LONG_BIAS = "LONG_BIAS"
    SHORT_BIAS = "SHORT_BIAS"
    NEUTRAL = "NEUTRAL"


class EligibilityReason(StrEnum):
    HALAL_AND_THRESHOLD = "halal_and_threshold"
    GATED_HARAM = "gated_haram"
    GATED_UNKNOWN = "gated_unknown"
    THRESHOLD_NOT_MET = "threshold_not_met"
    MTF_LONG = "mtf_long"
    MTF_SHORT = "mtf_short"
    MTF_MIXED = "mtf_mixed"
    MTF_NEUTRAL = "mtf_neutral"
    MTF_INSUFFICIENT = "mtf_insufficient"
    STRUCTURE_LONG = "structure_long"
    STRUCTURE_SHORT = "structure_short"
    STRUCTURE_RANGING = "structure_ranging"
    STRUCTURE_UNREADY = "structure_unready"
    STRUCTURE_EVENT_LONG = "structure_event_long"
    STRUCTURE_EVENT_SHORT = "structure_event_short"
    ORDER_BLOCK_LONG = "order_block_long"
    ORDER_BLOCK_SHORT = "order_block_short"
    ORDER_BLOCK_MISSING = "order_block_missing"
    OTE_LONG = "ote_long"
    OTE_SHORT = "ote_short"
    OTE_MISSING = "ote_missing"
    MSS_MISSING = "mss_missing"
    BREAKER_MISSING = "breaker_missing"
    MITIGATION_MISSING = "mitigation_missing"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    NO_DIRECTIONAL_EVIDENCE = "no_directional_evidence"


def current_observation(frame: ScoreSnapshot) -> ObservedCandle:
    return halal_observation(frame.upstream)


@dataclass(frozen=True, slots=True)
class SignalEligibility:
    """Whether this closed candle may be considered by a future signal engine."""

    status: EligibilityStatus
    bias: MarketBias
    classification: AssetClassification
    score_total: int
    threshold_passed: bool
    publish_threshold: int
    eligible: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, EligibilityStatus):
            raise AnalysisInputError("status must be ELIGIBLE or NOT_ELIGIBLE")
        if not isinstance(self.bias, MarketBias):
            raise AnalysisInputError("bias must be LONG_BIAS, SHORT_BIAS, or NEUTRAL")
        if not isinstance(self.classification, AssetClassification):
            raise AnalysisInputError("classification must be an AssetClassification")
        if type(self.score_total) is not int or not 0 <= self.score_total <= 100:
            raise AnalysisInputError("score_total must be an integer from 0 to 100")
        if type(self.threshold_passed) is not bool:
            raise AnalysisInputError("threshold_passed must be a bool")
        if type(self.publish_threshold) is not int or not 0 <= self.publish_threshold <= 100:
            raise AnalysisInputError("publish_threshold must be an integer from 0 to 100")
        if self.classification is not AssetClassification.HALAL:
            if self.status is EligibilityStatus.ELIGIBLE:
                raise AnalysisInputError("non-HALAL assets cannot be eligible")
            if self.bias is not MarketBias.NEUTRAL:
                raise AnalysisInputError("non-HALAL assets cannot carry a directional bias")
            if self.score_total != 0 or self.threshold_passed:
                raise AnalysisInputError("non-HALAL assets cannot pass a quality threshold")
        elif self.threshold_passed:
            if self.status is not EligibilityStatus.ELIGIBLE:
                raise AnalysisInputError("HALAL setups at or above threshold must be ELIGIBLE")
        elif self.status is EligibilityStatus.ELIGIBLE:
            raise AnalysisInputError("setups below the quality threshold cannot be ELIGIBLE")
        object.__setattr__(self, "eligible", self.status is EligibilityStatus.ELIGIBLE)


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    """One closed-candle eligibility record with reasons and evidence references."""

    settings: SignalEligibilityConfig
    eligibility: SignalEligibility
    reasons: tuple[EligibilityReason, ...]
    evidence: tuple[EvidenceReference, ...]
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SignalEligibilityConfig):
            raise AnalysisInputError("decision requires SignalEligibilityConfig")
        if not isinstance(self.eligibility, SignalEligibility):
            raise AnalysisInputError("decision requires SignalEligibility")
        if (
            not isinstance(self.reasons, tuple)
            or not self.reasons
            or not all(isinstance(item, EligibilityReason) for item in self.reasons)
        ):
            raise AnalysisInputError("reasons must be a nonempty tuple of EligibilityReason")
        if len(self.reasons) != len(set(self.reasons)):
            raise AnalysisInputError("duplicate eligibility reasons are forbidden")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, EvidenceReference) for item in self.evidence
        ):
            raise AnalysisInputError("evidence must be an immutable EvidenceReference tuple")
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise AnalysisInputError("duplicate evidence references are forbidden")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("decision requires provenance")
        if self.provenance.producer != "eligibility-decision":
            raise AnalysisInputError("decision producer must be eligibility-decision")


@dataclass(frozen=True, slots=True)
class EligibilitySnapshot:
    settings: SignalEligibilityConfig
    upstream: ScoreSnapshot
    decision: EligibilityDecision
    provenance: EvidenceProvenance
    status: EligibilityStatus = field(init=False)
    bias: MarketBias = field(init=False)
    eligible: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SignalEligibilityConfig) or not isinstance(
            self.upstream, ScoreSnapshot
        ):
            raise AnalysisInputError(
                "eligibility snapshot requires settings and an existing ScoreSnapshot"
            )
        if (
            not isinstance(self.decision, EligibilityDecision)
            or self.decision.settings != self.settings
        ):
            raise AnalysisInputError("eligibility snapshot must use this exact configuration")
        item = self.decision.eligibility
        if item.classification != self.upstream.upstream.classification:
            raise AnalysisInputError("eligibility classification must match the upstream filter")
        if (
            item.score_total != self.upstream.total
            or item.threshold_passed is not self.upstream.threshold_passed
            or item.publish_threshold != self.upstream.settings.publish_threshold
        ):
            raise AnalysisInputError("eligibility must copy the upstream integer score facts")
        if self.decision.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("decision must reuse the current consumed prefix")
        observation = current_observation(self.upstream)
        _metadata(self.decision.provenance, observation, (self.upstream.provenance.as_reference(),))
        _metadata(
            self.provenance,
            observation,
            (
                self.upstream.provenance.as_reference(),
                self.decision.provenance.as_reference(),
            ),
        )
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("eligibility snapshot must reuse the current consumed prefix")
        if self.provenance.series != self.upstream.provenance.series:
            raise AnalysisInputError("eligibility snapshot series must be the upstream series")
        if self.provenance.producer != "eligibility-frame":
            raise AnalysisInputError("snapshot producer must be eligibility-frame")
        object.__setattr__(self, "status", item.status)
        object.__setattr__(self, "bias", item.bias)
        object.__setattr__(self, "eligible", item.eligible)
