"""Immutable registry decisions and published eligibility snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter.classification import (
    AssetClassification,
    classify_asset,
    decision_reason,
    is_eligible,
)
from smcsignal.analysis.halal_filter.config import FilterMode, HalalFilterConfig, normalize_asset
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.mtf.models import MTFSnapshot
from smcsignal.analysis.provenance import EvidenceProvenance


def current_observation(frame: MTFSnapshot) -> ObservedCandle:
    return frame.upstream.upstream.observation


@dataclass(frozen=True, slots=True)
class HalalDecision:
    """One registry lookup for a normalized symbol; not a candle-derived feature."""

    settings: HalalFilterConfig
    symbol: str
    provenance: EvidenceProvenance
    classification: AssetClassification = field(init=False)
    eligible: bool = field(init=False)
    reason: str = field(init=False)
    mode: FilterMode = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, HalalFilterConfig):
            raise AnalysisInputError("halal decision requires HalalFilterConfig")
        symbol = normalize_asset(self.symbol, error=AnalysisInputError)
        object.__setattr__(self, "symbol", symbol)
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("halal decision requires provenance")
        if self.provenance.source_candles:
            raise AnalysisInputError("halal decision is registry evidence, not a candle source")
        series_symbol = normalize_asset(self.provenance.series.symbol, error=AnalysisInputError)
        if series_symbol != symbol:
            raise AnalysisInputError("halal decision symbol does not match its series")
        classification = classify_asset(symbol, self.settings)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "eligible", is_eligible(classification))
        object.__setattr__(self, "reason", decision_reason(symbol, self.settings))
        object.__setattr__(self, "mode", self.settings.mode)


@dataclass(frozen=True, slots=True)
class HalalSnapshot:
    settings: HalalFilterConfig
    upstream: MTFSnapshot
    decision: HalalDecision
    provenance: EvidenceProvenance
    classification: AssetClassification = field(init=False)
    eligible: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, HalalFilterConfig) or not isinstance(
            self.upstream, MTFSnapshot
        ):
            raise AnalysisInputError("halal snapshot requires settings and an existing MTF frame")
        if not isinstance(self.decision, HalalDecision) or self.decision.settings != self.settings:
            raise AnalysisInputError("halal snapshot decision must use this filter configuration")
        series = self.upstream.provenance.series
        if self.decision.symbol != normalize_asset(series.symbol, error=AnalysisInputError):
            raise AnalysisInputError("halal decision does not match the upstream series symbol")
        if self.decision.provenance.series != series:
            raise AnalysisInputError("halal decision series must match the upstream series")
        observation = current_observation(self.upstream)
        _metadata(
            self.provenance,
            observation,
            (self.upstream.provenance.as_reference(), self.decision.provenance.as_reference()),
        )
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("halal snapshot must reuse the current consumed prefix")
        if self.provenance.series != series:
            raise AnalysisInputError("halal snapshot series must be the upstream series")
        object.__setattr__(self, "classification", self.decision.classification)
        object.__setattr__(self, "eligible", self.decision.eligible)
