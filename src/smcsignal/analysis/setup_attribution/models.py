"""Immutable setup-attribution facts from existing signal evidence. Not strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    _instant,
)
from smcsignal.analysis.setup_attribution.config import SetupAttributionConfig
from smcsignal.analysis.setup_quality.models import ScoreBreakdown
from smcsignal.analysis.signal_engine.models import (
    SignalSnapshot,
    SignalStatus,
)
from smcsignal.analysis.signal_engine.models import (
    current_observation as signal_observation,
)


class SetupLabel(StrEnum):
    """Closed taxonomy of observed confluence facts, in canonical order.

    Each label is a factual observation of an already-published nested fact at
    the signal candle. Labels are not strategy names, ratings, or predictions.
    MSS, Breaker, and Mitigation facts are not reachable on the nested signal
    graph and therefore have no labels in attribution-v1.
    """

    BULLISH_DISPLACEMENT = "bullish_displacement"
    SWEEP_ASSOCIATED = "sweep_associated"
    FVG_BULLISH = "fvg_bullish"
    FVG_BEARISH = "fvg_bearish"
    ORDER_BLOCK_BULLISH = "order_block_bullish"
    ORDER_BLOCK_BEARISH = "order_block_bearish"
    INSIDE_OTE = "inside_ote"
    PD_DISCOUNT = "pd_discount"
    PD_PREMIUM = "pd_premium"
    PD_EQUILIBRIUM = "pd_equilibrium"
    MTF_BULLISH = "mtf_bullish"
    MTF_MIXED = "mtf_mixed"


def current_observation(frame: SignalSnapshot) -> ObservedCandle:
    """Delegate to the existing Phase 17 observation; no second data source."""

    return signal_observation(frame.upstream)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


@dataclass(frozen=True, slots=True)
class SetupAttribution:
    """One BUY_SIGNAL's observed confluence facts and copied context.

    Labels are derived deterministically from nested facts that already exist
    on the consumed signal frame. Outcomes, future candles, and invented
    strategy names never participate. The combination key is the labels joined
    in canonical order; it is a descriptive reporting bucket.
    """

    settings: SetupAttributionConfig
    attribution_id: str
    signal_id: str
    setup_identity: str
    symbol: str
    timeframe: str
    labels: tuple[SetupLabel, ...]
    score_total: int
    component_scores: ScoreBreakdown
    reference: CandleReference
    published_at: datetime
    provenance: EvidenceProvenance
    combination_key: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SetupAttributionConfig):
            raise AnalysisInputError("attribution requires SetupAttributionConfig")
        for name, value in (
            ("attribution_id", self.attribution_id),
            ("signal_id", self.signal_id),
            ("setup_identity", self.setup_identity),
            ("symbol", self.symbol),
            ("timeframe", self.timeframe),
        ):
            _text(value, name)
        if not isinstance(self.labels, tuple) or not all(
            isinstance(label, SetupLabel) for label in self.labels
        ):
            raise AnalysisInputError("labels must be a tuple of SetupLabel values")
        if len(self.labels) != len(set(self.labels)):
            raise AnalysisInputError("duplicate setup labels are forbidden")
        if list(self.labels) != [label for label in SetupLabel if label in set(self.labels)]:
            raise AnalysisInputError("labels must be in canonical taxonomy order")
        if type(self.score_total) is not int or not 0 <= self.score_total <= 100:
            raise AnalysisInputError("score_total must be an integer from 0 to 100")
        if not isinstance(self.component_scores, ScoreBreakdown):
            raise AnalysisInputError("component_scores must be a ScoreBreakdown")
        if self.component_scores.total != self.score_total:
            raise AnalysisInputError("score_total must match the copied SQS breakdown total")
        if not isinstance(self.reference, CandleReference):
            raise AnalysisInputError("attribution requires the signal CandleReference")
        object.__setattr__(self, "published_at", _instant(self.published_at, "published_at"))
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("attribution requires provenance")
        if self.provenance.producer != "attribution-record":
            raise AnalysisInputError("attribution producer must be attribution-record")
        if self.provenance.series != self.reference.series:
            raise AnalysisInputError("attribution provenance must use the signal series")
        if self.reference not in self.provenance.source_candles:
            raise AnalysisInputError("attribution provenance must include the signal candle")
        if self.provenance.available_at != self.published_at:
            raise AnalysisInputError("attribution is published at the signal cutoff")
        object.__setattr__(self, "combination_key", "+".join(label.value for label in self.labels))


@dataclass(frozen=True, slots=True)
class AttributionSnapshot:
    """Per-signal-frame attribution delta; BUY frames carry exactly one profile.

    The original Phase 17 SignalSnapshot instance is retained unchanged.
    Attribution is outcome-independent: this layer consumes signal frames only
    and never reads Phase 18 records.
    """

    settings: SetupAttributionConfig
    upstream: SignalSnapshot
    attribution: SetupAttribution | None
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SetupAttributionConfig) or not isinstance(
            self.upstream, SignalSnapshot
        ):
            raise AnalysisInputError(
                "attribution snapshot requires settings and an existing SignalSnapshot"
            )
        if (self.attribution is not None) is not (self.upstream.status is SignalStatus.BUY_SIGNAL):
            raise AnalysisInputError("attribution exists exactly on BUY_SIGNAL frames")
        if self.attribution is not None:
            if self.attribution.signal_id != self.upstream.signal_id:
                raise AnalysisInputError("attribution must track the upstream signal")
            if self.attribution.setup_identity != self.upstream.setup_identity:
                raise AnalysisInputError("attribution must copy the upstream setup identity")
            if self.attribution.settings != self.settings:
                raise AnalysisInputError("attribution must use this exact configuration")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("attribution snapshot requires provenance")
        if self.provenance.producer != "attribution-frame":
            raise AnalysisInputError("snapshot producer must be attribution-frame")
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("attribution snapshot must reuse the current prefix")
        if self.provenance.series != self.upstream.provenance.series:
            raise AnalysisInputError("attribution snapshot series must be the upstream series")
        observation = current_observation(self.upstream)
        dependencies = [self.upstream.provenance.as_reference()]
        if self.attribution is not None:
            dependencies.append(self.attribution.provenance.as_reference())
        _metadata(self.provenance, observation, tuple(dependencies))
