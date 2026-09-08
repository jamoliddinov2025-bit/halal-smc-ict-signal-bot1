"""Immutable integer score breakdown, per-candle score, and published snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter.models import (
    HalalSnapshot,
)
from smcsignal.analysis.halal_filter.models import (
    current_observation as mtf_observation,
)
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.provenance import EvidenceProvenance
from smcsignal.analysis.setup_quality.config import MAX_SCORE, SetupQualityConfig


class ScoreComponent(StrEnum):
    HALAL = "halal"
    MTF = "mtf"
    MSS = "mss"
    DISPLACEMENT = "displacement"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    FVG = "fvg"
    ORDER_BLOCK = "order_block"
    BREAKER_BLOCK = "breaker_block"
    MITIGATION_BLOCK = "mitigation_block"
    PREMIUM_DISCOUNT = "premium_discount"
    OTE = "ote"


WEIGHTS: dict[ScoreComponent, int] = {
    ScoreComponent.HALAL: 10,
    ScoreComponent.MTF: 15,
    ScoreComponent.MSS: 10,
    ScoreComponent.DISPLACEMENT: 12,
    ScoreComponent.LIQUIDITY_SWEEP: 10,
    ScoreComponent.FVG: 8,
    ScoreComponent.ORDER_BLOCK: 10,
    ScoreComponent.BREAKER_BLOCK: 5,
    ScoreComponent.MITIGATION_BLOCK: 5,
    ScoreComponent.PREMIUM_DISCOUNT: 8,
    ScoreComponent.OTE: 7,
}


def current_observation(frame: HalalSnapshot) -> ObservedCandle:
    return mtf_observation(frame.upstream)


def _require_weights() -> None:
    if sum(WEIGHTS.values()) != MAX_SCORE:
        raise AnalysisInputError("sqs-v1 component weights must sum to 100")
    if tuple(WEIGHTS) != tuple(ScoreComponent):
        raise AnalysisInputError("sqs-v1 weights must follow ScoreComponent order")


_require_weights()


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Integer points per component. Missing evidence is 0, never a hard failure."""

    halal: int
    mtf: int
    mss: int
    displacement: int
    liquidity_sweep: int
    fvg: int
    order_block: int
    breaker_block: int
    mitigation_block: int
    premium_discount: int
    ote: int
    reasons: tuple[str, ...]
    total: int = field(init=False)

    def __post_init__(self) -> None:
        values = tuple(getattr(self, component.value) for component in ScoreComponent)
        for component, value in zip(ScoreComponent, values, strict=True):
            if type(value) is not int or not 0 <= value <= WEIGHTS[component]:
                raise AnalysisInputError(
                    f"{component.value} points must be an integer from 0 to {WEIGHTS[component]}"
                )
        if not isinstance(self.reasons, tuple) or len(self.reasons) != len(ScoreComponent):
            raise AnalysisInputError("reasons must have one nonempty string per component")
        if not all(type(reason) is str and reason for reason in self.reasons):
            raise AnalysisInputError("each component reason must be a nonempty string")
        total = sum(values)
        if not 0 <= total <= MAX_SCORE:
            raise AnalysisInputError("breakdown total must be an integer from 0 to 100")
        object.__setattr__(self, "total", total)

    @property
    def contributions(self) -> tuple[tuple[ScoreComponent, int, str], ...]:
        values = tuple(getattr(self, component.value) for component in ScoreComponent)
        return tuple(zip(ScoreComponent, values, self.reasons, strict=True))


@dataclass(frozen=True, slots=True)
class SetupQualityScore:
    """0–100 integer quality score. Not a signal, probability, or ranking."""

    settings: SetupQualityConfig
    breakdown: ScoreBreakdown
    eligible: bool
    provenance: EvidenceProvenance
    total: int = field(init=False)
    threshold_passed: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SetupQualityConfig):
            raise AnalysisInputError("score requires SetupQualityConfig")
        if not isinstance(self.breakdown, ScoreBreakdown):
            raise AnalysisInputError("score requires a ScoreBreakdown")
        if type(self.eligible) is not bool:
            raise AnalysisInputError("eligible must be a bool")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("score requires provenance")
        if self.provenance.producer != "sqs-score":
            raise AnalysisInputError("score producer must be sqs-score")
        if not self.eligible and self.breakdown.total != 0:
            raise AnalysisInputError("ineligible assets must score 0")
        total = self.breakdown.total
        object.__setattr__(self, "total", total)
        object.__setattr__(
            self,
            "threshold_passed",
            self.eligible and total >= self.settings.publish_threshold,
        )


@dataclass(frozen=True, slots=True)
class ScoreSnapshot:
    settings: SetupQualityConfig
    upstream: HalalSnapshot
    score: SetupQualityScore
    provenance: EvidenceProvenance
    total: int = field(init=False)
    threshold_passed: bool = field(init=False)
    eligible: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SetupQualityConfig) or not isinstance(
            self.upstream, HalalSnapshot
        ):
            raise AnalysisInputError(
                "score snapshot requires settings and an existing HalalSnapshot"
            )
        if not isinstance(self.score, SetupQualityScore) or self.score.settings != self.settings:
            raise AnalysisInputError("score snapshot must use this exact configuration")
        if self.score.eligible is not self.upstream.eligible:
            raise AnalysisInputError("score eligibility must match the upstream filter")
        if self.score.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("score must reuse the current consumed prefix")
        observation = current_observation(self.upstream)
        _metadata(self.score.provenance, observation, (self.upstream.provenance.as_reference(),))
        _metadata(
            self.provenance,
            observation,
            (self.upstream.provenance.as_reference(), self.score.provenance.as_reference()),
        )
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("score snapshot must reuse the current consumed prefix")
        if self.provenance.series != self.upstream.provenance.series:
            raise AnalysisInputError("score snapshot series must be the upstream series")
        if self.provenance.producer != "sqs-frame":
            raise AnalysisInputError("snapshot producer must be sqs-frame")
        object.__setattr__(self, "total", self.score.total)
        object.__setattr__(self, "threshold_passed", self.score.threshold_passed)
        object.__setattr__(self, "eligible", self.upstream.eligible)
