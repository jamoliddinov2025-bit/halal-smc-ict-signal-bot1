"""Map already-published eligibility to a spot publication. No look-ahead or new bias."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter.classification import AssetClassification
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.mtf.calculation import MTFDirection
from smcsignal.analysis.ote.calculation import OTEClassification
from smcsignal.analysis.premium_discount.calculation import PDClassification
from smcsignal.analysis.setup_quality.calculation import (
    nested_displacement,
    nested_mtf,
    nested_ote,
    nested_pd,
)
from smcsignal.analysis.signal_eligibility.models import (
    EligibilitySnapshot,
    EligibilityStatus,
    MarketBias,
    SignalEligibility,
)
from smcsignal.analysis.signal_engine.config import SignalEngineConfig
from smcsignal.analysis.signal_engine.evidence import (
    candidate_provenance,
    collect_evidence,
    configuration_artifact,
    setup_identity,
    snapshot_provenance,
)
from smcsignal.analysis.signal_engine.models import (
    Signal,
    SignalCandidate,
    SignalDirection,
    SignalReason,
    SignalSnapshot,
    SignalStatus,
    current_observation,
)


def status_for(eligibility: SignalEligibility) -> SignalStatus:
    """Consume Phase 16 facts only. Spot-only: never invent a short trade."""

    if (
        eligibility.classification is not AssetClassification.HALAL
        or eligibility.status is not EligibilityStatus.ELIGIBLE
        or not eligibility.threshold_passed
    ):
        return SignalStatus.NO_SIGNAL
    if eligibility.bias is MarketBias.SHORT_BIAS:
        return SignalStatus.BEARISH_AVOID
    if eligibility.bias is MarketBias.LONG_BIAS:
        return SignalStatus.BUY_SIGNAL
    return SignalStatus.NO_SIGNAL


def direction_for(status: SignalStatus) -> SignalDirection:
    return SignalDirection.LONG if status is SignalStatus.BUY_SIGNAL else SignalDirection.NONE


def reasons_for(
    frame: EligibilitySnapshot, status: SignalStatus, *, duplicate: bool = False
) -> tuple[SignalReason, ...]:
    """Explain with already-published facts only. Do not invent missing confluence."""

    if duplicate:
        return (SignalReason.DUPLICATE_SETUP,)
    eligibility = frame.decision.eligibility
    if status is SignalStatus.NO_SIGNAL:
        if eligibility.classification is not AssetClassification.HALAL:
            return (SignalReason.NOT_HALAL,)
        if not eligibility.threshold_passed:
            return (SignalReason.SQS_BELOW_THRESHOLD,)
        if eligibility.bias is MarketBias.NEUTRAL:
            return (SignalReason.NEUTRAL_BIAS,)
        return (SignalReason.MISSING_REQUIRED_EVIDENCE,)
    if status is SignalStatus.BEARISH_AVOID:
        return (
            SignalReason.HALAL_ASSET,
            SignalReason.SQS_THRESHOLD_PASSED,
            SignalReason.BEARISH_SPOT_AVOID,
        )
    halal = frame.upstream.upstream
    mtf = nested_mtf(halal)
    reasons: list[SignalReason] = [
        SignalReason.HALAL_ASSET,
        SignalReason.SQS_THRESHOLD_PASSED,
        SignalReason.LONG_BIAS,
    ]
    if mtf.direction is MTFDirection.BULLISH:
        reasons.append(SignalReason.MTF_ALIGNMENT)
    if any(
        event.direction is TrendDirection.BULLISH for event in nested_displacement(halal).events
    ):
        reasons.append(SignalReason.BULLISH_DISPLACEMENT)
    if nested_pd(halal).classification is PDClassification.DISCOUNT:
        reasons.append(SignalReason.DISCOUNT_CONTEXT)
    if nested_ote(halal).classification is OTEClassification.INSIDE_OTE:
        reasons.append(SignalReason.OTE_CONTEXT)
    return tuple(reasons)


def build_signal_snapshot(
    frame: EligibilitySnapshot,
    config: SignalEngineConfig | None = None,
    *,
    seen_setups: AbstractSet[str] | None = None,
) -> SignalSnapshot:
    """One-frame mapping from existing eligibility. Duplicate collapsing is opt-in."""

    if not isinstance(frame, EligibilitySnapshot):
        raise AnalysisInputError("signal engine consumes existing EligibilitySnapshot frames")
    settings = config if config is not None else SignalEngineConfig()
    if not isinstance(settings, SignalEngineConfig):
        raise AnalysisInputError("config must be SignalEngineConfig")
    eligibility = frame.decision.eligibility
    if settings.publish_threshold != eligibility.publish_threshold:
        raise AnalysisInputError(
            "signal engine publish_threshold must match the consumed SQS threshold"
        )
    observation = current_observation(frame)
    status = status_for(eligibility)
    evidence = collect_evidence(frame)
    identity = setup_identity(frame)
    duplicate = False
    if status is SignalStatus.BUY_SIGNAL and not evidence:
        status = SignalStatus.NO_SIGNAL
    elif status is SignalStatus.BUY_SIGNAL and seen_setups is not None and identity in seen_setups:
        status = SignalStatus.NO_SIGNAL
        duplicate = True
    reasons = reasons_for(frame, status, duplicate=duplicate)
    latest_evidence = max(
        (item.available_at for item in evidence), default=observation.available_at
    )
    signal = Signal(
        status,
        direction_for(status),
        eligibility.classification,
        eligibility.status,
        eligibility.bias,
        eligibility.score_total,
        eligibility.threshold_passed,
        eligibility.publish_threshold,
        observation.reference.series.symbol,
        observation.reference.series.timeframe,
        identity,
        observation.reference,
        observation.reference.opened_at,
        observation.reference.closed_at,
        latest_evidence,
        frame.provenance.available_at,
        observation.available_at,
    )
    artifact = configuration_artifact(settings)
    config_hash = sha256(artifact).hexdigest()
    candidate = SignalCandidate(
        settings,
        signal,
        reasons,
        evidence,
        candidate_provenance(frame, signal, reasons, evidence, config_hash),
    )
    return SignalSnapshot(
        settings, frame, candidate, snapshot_provenance(frame, candidate, config_hash)
    )
