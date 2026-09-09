"""Derive setup labels from existing nested facts. No outcomes, no invention."""

from __future__ import annotations

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.mtf.calculation import MTFDirection
from smcsignal.analysis.ote.calculation import OTEClassification
from smcsignal.analysis.premium_discount.calculation import PDClassification
from smcsignal.analysis.setup_attribution.config import (
    METHODOLOGY_VERSION,
    SetupAttributionConfig,
)
from smcsignal.analysis.setup_attribution.evidence import (
    attribution_identity,
    record_provenance,
)
from smcsignal.analysis.setup_attribution.models import (
    SetupAttribution,
    SetupLabel,
    current_observation,
)
from smcsignal.analysis.setup_quality.calculation import (
    nested_displacement,
    nested_fvg,
    nested_mtf,
    nested_order_block,
    nested_ote,
    nested_pd,
)
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus


def labels_for(frame: SignalSnapshot) -> tuple[SetupLabel, ...]:
    """Project already-published nested facts onto the closed taxonomy.

    Each label maps to one named nested fact at the signal candle. Missing or
    INSUFFICIENT_CONTEXT facts produce no label; nothing is invented. MSS,
    Breaker, and Mitigation facts are not on this nested graph.
    """

    if not isinstance(frame, SignalSnapshot):
        raise AnalysisInputError("attribution consumes existing SignalSnapshot frames")
    halal = frame.upstream.upstream.upstream
    displacement = nested_displacement(halal)
    gaps = nested_fvg(halal)
    blocks = nested_order_block(halal)
    ote = nested_ote(halal)
    premium_discount = nested_pd(halal)
    multi_timeframe = nested_mtf(halal)
    labels: set[SetupLabel] = set()
    if any(event.direction is TrendDirection.BULLISH for event in displacement.events):
        labels.add(SetupLabel.BULLISH_DISPLACEMENT)
    if any(event.preceding_sweeps for event in displacement.events):
        labels.add(SetupLabel.SWEEP_ASSOCIATED)
    if any(event.direction is TrendDirection.BULLISH for event in gaps.events):
        labels.add(SetupLabel.FVG_BULLISH)
    if any(event.direction is TrendDirection.BEARISH for event in gaps.events):
        labels.add(SetupLabel.FVG_BEARISH)
    if any(event.direction is TrendDirection.BULLISH for event in blocks.events):
        labels.add(SetupLabel.ORDER_BLOCK_BULLISH)
    if any(event.direction is TrendDirection.BEARISH for event in blocks.events):
        labels.add(SetupLabel.ORDER_BLOCK_BEARISH)
    if ote.classification is OTEClassification.INSIDE_OTE:
        labels.add(SetupLabel.INSIDE_OTE)
    if premium_discount.classification is PDClassification.DISCOUNT:
        labels.add(SetupLabel.PD_DISCOUNT)
    if premium_discount.classification is PDClassification.PREMIUM:
        labels.add(SetupLabel.PD_PREMIUM)
    if premium_discount.classification is PDClassification.EQUILIBRIUM:
        labels.add(SetupLabel.PD_EQUILIBRIUM)
    if multi_timeframe.direction is MTFDirection.BULLISH:
        labels.add(SetupLabel.MTF_BULLISH)
    if multi_timeframe.direction is MTFDirection.MIXED:
        labels.add(SetupLabel.MTF_MIXED)
    return tuple(label for label in SetupLabel if label in labels)


def build_attribution(
    frame: SignalSnapshot, settings: SetupAttributionConfig, config_hash: str
) -> SetupAttribution:
    """One deterministic attribution profile for one BUY_SIGNAL frame."""

    if not isinstance(frame, SignalSnapshot):
        raise AnalysisInputError("attribution consumes existing SignalSnapshot frames")
    if not isinstance(settings, SetupAttributionConfig):
        raise AnalysisInputError("settings must be SetupAttributionConfig")
    if frame.status is not SignalStatus.BUY_SIGNAL:
        raise AnalysisInputError("attribution is built from BUY_SIGNAL frames only")
    labels = labels_for(frame)
    observation = current_observation(frame)
    return SetupAttribution(
        settings=settings,
        attribution_id=attribution_identity(frame, labels, settings),
        signal_id=frame.signal_id,
        setup_identity=frame.setup_identity,
        symbol=observation.reference.series.symbol,
        timeframe=observation.reference.series.timeframe,
        labels=labels,
        score_total=frame.candidate.signal.score_total,
        component_scores=frame.upstream.upstream.score.breakdown,
        reference=observation.reference,
        published_at=observation.available_at,
        provenance=record_provenance(frame, labels, settings, config_hash),
    )


def methodology_version() -> str:
    """Expose the frozen methodology id for downstream evidence keys."""

    return METHODOLOGY_VERSION
