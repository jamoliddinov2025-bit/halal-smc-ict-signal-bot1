"""Immutable spot signal facts, candidates, and published snapshots. Not orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter.classification import AssetClassification
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.provenance import CandleReference, EvidenceProvenance, EvidenceReference
from smcsignal.analysis.signal_eligibility.models import (
    EligibilitySnapshot,
    EligibilityStatus,
    MarketBias,
)
from smcsignal.analysis.signal_eligibility.models import (
    current_observation as eligibility_observation,
)
from smcsignal.analysis.signal_engine.config import SignalEngineConfig


class SignalStatus(StrEnum):
    BUY_SIGNAL = "BUY_SIGNAL"
    BEARISH_AVOID = "BEARISH_AVOID"
    NO_SIGNAL = "NO_SIGNAL"


class SignalDirection(StrEnum):
    LONG = "LONG"
    NONE = "NONE"


class SignalReason(StrEnum):
    HALAL_ASSET = "halal_asset"
    SQS_THRESHOLD_PASSED = "sqs_threshold_passed"
    LONG_BIAS = "long_bias"
    MTF_ALIGNMENT = "mtf_alignment"
    BULLISH_MSS = "bullish_mss"
    BULLISH_DISPLACEMENT = "bullish_displacement"
    DISCOUNT_CONTEXT = "discount_context"
    OTE_CONTEXT = "ote_context"
    NOT_HALAL = "not_halal"
    SQS_BELOW_THRESHOLD = "sqs_below_threshold"
    NEUTRAL_BIAS = "neutral_bias"
    BEARISH_SPOT_AVOID = "bearish_spot_avoid"
    MISSING_REQUIRED_EVIDENCE = "missing_required_evidence"
    DUPLICATE_SETUP = "duplicate_setup"


def current_observation(frame: EligibilitySnapshot) -> ObservedCandle:
    return eligibility_observation(frame.upstream)


@dataclass(frozen=True, slots=True)
class Signal:
    """One closed-candle spot publication. Never a short, entry, or probability."""

    status: SignalStatus
    direction: SignalDirection
    classification: AssetClassification
    eligibility_status: EligibilityStatus
    bias: MarketBias
    score_total: int
    threshold_passed: bool
    publish_threshold: int
    symbol: str
    timeframe: str
    setup_identity: str
    candle: CandleReference
    candle_opened_at: datetime
    candle_closed_at: datetime
    evidence_available_at: datetime
    eligibility_available_at: datetime
    published_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.status, SignalStatus):
            raise AnalysisInputError("status must be BUY_SIGNAL, BEARISH_AVOID, or NO_SIGNAL")
        if not isinstance(self.direction, SignalDirection):
            raise AnalysisInputError("direction must be LONG or NONE")
        if not isinstance(self.classification, AssetClassification):
            raise AnalysisInputError("classification must be an AssetClassification")
        if not isinstance(self.eligibility_status, EligibilityStatus):
            raise AnalysisInputError("eligibility_status must be ELIGIBLE or NOT_ELIGIBLE")
        if not isinstance(self.bias, MarketBias):
            raise AnalysisInputError("bias must be LONG_BIAS, SHORT_BIAS, or NEUTRAL")
        if type(self.score_total) is not int or not 0 <= self.score_total <= 100:
            raise AnalysisInputError("score_total must be an integer from 0 to 100")
        if type(self.threshold_passed) is not bool:
            raise AnalysisInputError("threshold_passed must be a bool")
        if type(self.publish_threshold) is not int or not 0 <= self.publish_threshold <= 100:
            raise AnalysisInputError("publish_threshold must be an integer from 0 to 100")
        if (
            not isinstance(self.symbol, str)
            or not self.symbol.strip()
            or self.symbol != self.symbol.strip()
        ):
            raise AnalysisInputError("symbol must be a nonempty, trimmed string")
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise AnalysisInputError("timeframe must be a nonempty, trimmed string")
        if (
            not isinstance(self.setup_identity, str)
            or not self.setup_identity.strip()
            or self.setup_identity != self.setup_identity.strip()
        ):
            raise AnalysisInputError("setup_identity must be a nonempty, trimmed string")
        if not isinstance(self.candle, CandleReference):
            raise AnalysisInputError("signal requires a primary CandleReference")
        if (
            self.candle.opened_at != self.candle_opened_at
            or self.candle.closed_at != self.candle_closed_at
        ):
            raise AnalysisInputError("candle open/close must match the primary candle reference")
        if self.published_at < self.candle.closed_at:
            raise AnalysisInputError(
                "a signal cannot be published before the primary candle closes"
            )
        if self.eligibility_available_at > self.published_at:
            raise AnalysisInputError("eligibility is not available at signal publication")
        if self.evidence_available_at > self.published_at:
            raise AnalysisInputError("required evidence is not available at signal publication")
        if self.classification is not AssetClassification.HALAL:
            if self.status is not SignalStatus.NO_SIGNAL:
                raise AnalysisInputError(
                    "non-HALAL assets cannot publish BUY_SIGNAL or BEARISH_AVOID"
                )
            if self.direction is not SignalDirection.NONE:
                raise AnalysisInputError(
                    "non-HALAL assets cannot carry a long publication direction"
                )
        if self.status is SignalStatus.BUY_SIGNAL:
            if (
                self.direction is not SignalDirection.LONG
                or self.bias is not MarketBias.LONG_BIAS
                or self.eligibility_status is not EligibilityStatus.ELIGIBLE
                or not self.threshold_passed
                or self.score_total < self.publish_threshold
            ):
                raise AnalysisInputError(
                    "BUY_SIGNAL requires HALAL, ELIGIBLE, LONG_BIAS, and a passed SQS threshold"
                )
        elif self.status is SignalStatus.BEARISH_AVOID:
            if (
                self.direction is not SignalDirection.NONE
                or self.bias is not MarketBias.SHORT_BIAS
                or self.eligibility_status is not EligibilityStatus.ELIGIBLE
                or not self.threshold_passed
            ):
                raise AnalysisInputError(
                    "BEARISH_AVOID requires an eligible HALAL SHORT_BIAS and never a short trade"
                )
        elif self.direction is not SignalDirection.NONE:
            raise AnalysisInputError("NO_SIGNAL cannot carry a long publication direction")


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    """One closed-candle signal record with reasons and evidence references."""

    settings: SignalEngineConfig
    signal: Signal
    reasons: tuple[SignalReason, ...]
    evidence: tuple[EvidenceReference, ...]
    provenance: EvidenceProvenance
    signal_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SignalEngineConfig):
            raise AnalysisInputError("candidate requires SignalEngineConfig")
        if not isinstance(self.signal, Signal):
            raise AnalysisInputError("candidate requires Signal")
        if self.settings.publish_threshold != self.signal.publish_threshold:
            raise AnalysisInputError(
                "signal engine threshold must match the consumed SQS threshold"
            )
        if (
            not isinstance(self.reasons, tuple)
            or not self.reasons
            or not all(isinstance(item, SignalReason) for item in self.reasons)
        ):
            raise AnalysisInputError("reasons must be a nonempty tuple of SignalReason")
        if len(self.reasons) != len(set(self.reasons)):
            raise AnalysisInputError("duplicate signal reasons are forbidden")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, EvidenceReference) for item in self.evidence
        ):
            raise AnalysisInputError("evidence must be an immutable EvidenceReference tuple")
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise AnalysisInputError("duplicate evidence references are forbidden")
        if self.signal.status is SignalStatus.BUY_SIGNAL and not self.evidence:
            raise AnalysisInputError("BUY_SIGNAL requires already-available evidence references")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("candidate requires provenance")
        if self.provenance.producer != "signal-candidate":
            raise AnalysisInputError("candidate producer must be signal-candidate")
        object.__setattr__(self, "signal_id", self.provenance.evidence_id)


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    settings: SignalEngineConfig
    upstream: EligibilitySnapshot
    candidate: SignalCandidate
    provenance: EvidenceProvenance
    status: SignalStatus = field(init=False)
    direction: SignalDirection = field(init=False)
    signal_id: str = field(init=False)
    setup_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SignalEngineConfig) or not isinstance(
            self.upstream, EligibilitySnapshot
        ):
            raise AnalysisInputError(
                "signal snapshot requires settings and an existing EligibilitySnapshot"
            )
        if (
            not isinstance(self.candidate, SignalCandidate)
            or self.candidate.settings != self.settings
        ):
            raise AnalysisInputError("signal snapshot must use this exact configuration")
        item = self.candidate.signal
        eligibility = self.upstream.decision.eligibility
        if item.classification != eligibility.classification:
            raise AnalysisInputError("signal classification must match the upstream filter")
        if (
            item.eligibility_status is not eligibility.status
            or item.bias is not eligibility.bias
            or item.score_total != eligibility.score_total
            or item.threshold_passed is not eligibility.threshold_passed
            or item.publish_threshold != eligibility.publish_threshold
        ):
            raise AnalysisInputError("signal must copy the upstream eligibility and SQS facts")
        if item.publish_threshold != self.settings.publish_threshold:
            raise AnalysisInputError(
                "signal engine threshold must match the consumed SQS threshold"
            )
        if (
            self.candidate.provenance.input_prefix_hash
            != self.upstream.provenance.input_prefix_hash
        ):
            raise AnalysisInputError("candidate must reuse the current consumed prefix")
        observation = current_observation(self.upstream)
        if item.candle != observation.reference:
            raise AnalysisInputError("signal candle must be the current primary observation")
        if item.symbol != observation.reference.series.symbol:
            raise AnalysisInputError("signal symbol must match the upstream series")
        if item.timeframe != observation.reference.series.timeframe:
            raise AnalysisInputError("signal timeframe must match the upstream series")
        _metadata(
            self.candidate.provenance, observation, (self.upstream.provenance.as_reference(),)
        )
        _metadata(
            self.provenance,
            observation,
            (
                self.upstream.provenance.as_reference(),
                self.candidate.provenance.as_reference(),
            ),
        )
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("signal snapshot must reuse the current consumed prefix")
        if self.provenance.series != self.upstream.provenance.series:
            raise AnalysisInputError("signal snapshot series must be the upstream series")
        if self.provenance.producer != "signal-frame":
            raise AnalysisInputError("snapshot producer must be signal-frame")
        object.__setattr__(self, "status", item.status)
        object.__setattr__(self, "direction", item.direction)
        object.__setattr__(self, "signal_id", self.candidate.signal_id)
        object.__setattr__(self, "setup_identity", item.setup_identity)
