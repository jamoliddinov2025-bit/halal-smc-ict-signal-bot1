"""Map already-published nested facts to bias and eligibility. No look-ahead."""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from smcsignal.analysis.halal_filter.classification import AssetClassification
from smcsignal.analysis.models import TrendDirection, TrendState
from smcsignal.analysis.mtf.calculation import MTFDirection
from smcsignal.analysis.ote.calculation import OTEClassification, OTEDirection
from smcsignal.analysis.provenance import EvidenceReference
from smcsignal.analysis.setup_quality.calculation import (
    nested_displacement,
    nested_mtf,
    nested_order_block,
    nested_ote,
)
from smcsignal.analysis.setup_quality.models import ScoreSnapshot
from smcsignal.analysis.signal_eligibility.models import (
    EligibilityReason,
    EligibilityStatus,
    MarketBias,
    SignalEligibility,
    current_observation,
)


class DirectionalVote(NamedTuple):
    source: str
    bias: MarketBias | None
    reason: EligibilityReason
    mixed: bool = False


class Evaluation(NamedTuple):
    eligibility: SignalEligibility
    reasons: tuple[EligibilityReason, ...]
    evidence: tuple[EvidenceReference, ...]


def vote_mtf(direction: MTFDirection) -> DirectionalVote:
    if direction is MTFDirection.BULLISH:
        return DirectionalVote("mtf", MarketBias.LONG_BIAS, EligibilityReason.MTF_LONG)
    if direction is MTFDirection.BEARISH:
        return DirectionalVote("mtf", MarketBias.SHORT_BIAS, EligibilityReason.MTF_SHORT)
    if direction is MTFDirection.MIXED:
        return DirectionalVote("mtf", None, EligibilityReason.MTF_MIXED, mixed=True)
    if direction is MTFDirection.NEUTRAL:
        return DirectionalVote("mtf", None, EligibilityReason.MTF_NEUTRAL)
    return DirectionalVote("mtf", None, EligibilityReason.MTF_INSUFFICIENT)


def vote_trend(trend: TrendState) -> DirectionalVote:
    if not trend.ready:
        return DirectionalVote("structure", None, EligibilityReason.STRUCTURE_UNREADY)
    if trend.direction is TrendDirection.BULLISH:
        return DirectionalVote("structure", MarketBias.LONG_BIAS, EligibilityReason.STRUCTURE_LONG)
    if trend.direction is TrendDirection.BEARISH:
        return DirectionalVote(
            "structure", MarketBias.SHORT_BIAS, EligibilityReason.STRUCTURE_SHORT
        )
    return DirectionalVote("structure", None, EligibilityReason.STRUCTURE_RANGING)


def vote_structure_event(direction: TrendDirection) -> DirectionalVote:
    if direction is TrendDirection.BULLISH:
        return DirectionalVote(
            "structure_event", MarketBias.LONG_BIAS, EligibilityReason.STRUCTURE_EVENT_LONG
        )
    if direction is TrendDirection.BEARISH:
        return DirectionalVote(
            "structure_event", MarketBias.SHORT_BIAS, EligibilityReason.STRUCTURE_EVENT_SHORT
        )
    return DirectionalVote("structure_event", None, EligibilityReason.STRUCTURE_RANGING)


def vote_order_block(direction: TrendDirection | None) -> DirectionalVote:
    if direction is TrendDirection.BULLISH:
        return DirectionalVote(
            "order_block", MarketBias.LONG_BIAS, EligibilityReason.ORDER_BLOCK_LONG
        )
    if direction is TrendDirection.BEARISH:
        return DirectionalVote(
            "order_block", MarketBias.SHORT_BIAS, EligibilityReason.ORDER_BLOCK_SHORT
        )
    return DirectionalVote("order_block", None, EligibilityReason.ORDER_BLOCK_MISSING)


def vote_ote(classification: OTEClassification, direction: OTEDirection | None) -> DirectionalVote:
    if classification is OTEClassification.INSIDE_OTE and direction is OTEDirection.BULLISH:
        return DirectionalVote("ote", MarketBias.LONG_BIAS, EligibilityReason.OTE_LONG)
    if classification is OTEClassification.INSIDE_OTE and direction is OTEDirection.BEARISH:
        return DirectionalVote("ote", MarketBias.SHORT_BIAS, EligibilityReason.OTE_SHORT)
    return DirectionalVote("ote", None, EligibilityReason.OTE_MISSING)


def resolve_bias(votes: tuple[DirectionalVote, ...]) -> MarketBias:
    if any(item.mixed for item in votes):
        return MarketBias.NEUTRAL
    directions = {item.bias for item in votes if item.bias is not None}
    if MarketBias.LONG_BIAS in directions and MarketBias.SHORT_BIAS in directions:
        return MarketBias.NEUTRAL
    if MarketBias.LONG_BIAS in directions:
        return MarketBias.LONG_BIAS
    if MarketBias.SHORT_BIAS in directions:
        return MarketBias.SHORT_BIAS
    return MarketBias.NEUTRAL


def _known(reference: EvidenceReference, cutoff: datetime) -> bool:
    return reference.available_at <= cutoff


def collect_votes(frame: ScoreSnapshot) -> tuple[DirectionalVote, ...]:
    """Read nested facts only. Parallel MSS/Breaker/Mitigation consumers abstain."""
    halal = frame.upstream
    mtf = nested_mtf(halal)
    ote = nested_ote(halal)
    blocks = nested_order_block(halal).events
    structure = nested_displacement(halal).liquidity.context.snapshot
    ob_direction = blocks[0].direction if blocks else None
    zone_direction = ote.zone.direction if ote.zone is not None else None
    votes = [
        vote_mtf(mtf.direction),
        vote_trend(structure.trend),
        *(vote_structure_event(event.direction) for event in structure.events),
        vote_order_block(ob_direction),
        vote_ote(ote.classification, zone_direction),
        DirectionalVote("mss", None, EligibilityReason.MSS_MISSING),
        DirectionalVote("breaker", None, EligibilityReason.BREAKER_MISSING),
        DirectionalVote("mitigation", None, EligibilityReason.MITIGATION_MISSING),
    ]
    return tuple(votes)


def collect_evidence(frame: ScoreSnapshot) -> tuple[EvidenceReference, ...]:
    observation = current_observation(frame)
    cutoff = observation.available_at
    halal = frame.upstream
    ote = nested_ote(halal)
    structure = nested_displacement(halal).liquidity.context
    refs = [
        frame.provenance.as_reference(),
        frame.score.provenance.as_reference(),
        halal.provenance.as_reference(),
        nested_mtf(halal).provenance.as_reference(),
        ote.provenance.as_reference(),
        structure.provenance.as_reference(),
        *(block.provenance.as_reference() for block in nested_order_block(halal).events),
    ]
    known = tuple(dict.fromkeys(item for item in refs if _known(item, cutoff)))
    return known


def evaluate(frame: ScoreSnapshot) -> Evaluation:
    """HALAL plus threshold for eligibility; conflicting nested votes stay NEUTRAL."""
    votes = collect_votes(frame)
    evidence = collect_evidence(frame)
    classification = frame.upstream.classification
    if classification is AssetClassification.HARAM:
        eligibility = SignalEligibility(
            EligibilityStatus.NOT_ELIGIBLE,
            MarketBias.NEUTRAL,
            classification,
            frame.total,
            frame.threshold_passed,
            frame.settings.publish_threshold,
        )
        return Evaluation(eligibility, (EligibilityReason.GATED_HARAM,), evidence)
    if classification is AssetClassification.UNKNOWN:
        eligibility = SignalEligibility(
            EligibilityStatus.NOT_ELIGIBLE,
            MarketBias.NEUTRAL,
            classification,
            frame.total,
            frame.threshold_passed,
            frame.settings.publish_threshold,
        )
        return Evaluation(eligibility, (EligibilityReason.GATED_UNKNOWN,), evidence)

    bias = resolve_bias(votes)
    direction_reasons = tuple(item.reason for item in votes)
    closing: tuple[EligibilityReason, ...]
    if bias is MarketBias.NEUTRAL:
        if any(item.mixed for item in votes) or (
            MarketBias.LONG_BIAS in {item.bias for item in votes}
            and MarketBias.SHORT_BIAS in {item.bias for item in votes}
        ):
            closing = (EligibilityReason.CONFLICTING_EVIDENCE,)
        else:
            closing = (EligibilityReason.NO_DIRECTIONAL_EVIDENCE,)
    else:
        closing = ()
    if frame.threshold_passed:
        status = EligibilityStatus.ELIGIBLE
        head = (EligibilityReason.HALAL_AND_THRESHOLD,)
    else:
        status = EligibilityStatus.NOT_ELIGIBLE
        head = (EligibilityReason.THRESHOLD_NOT_MET,)
    reasons = tuple(dict.fromkeys((*head, *direction_reasons, *closing)))
    eligibility = SignalEligibility(
        status,
        bias,
        classification,
        frame.total,
        frame.threshold_passed,
        frame.settings.publish_threshold,
    )
    return Evaluation(eligibility, reasons, evidence)
