"""Immutable indicator context frames. Descriptive values, never gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.indicators.config import IndicatorsConfig
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.provenance import EvidenceProvenance


def current_observation(frame: DisplacementSnapshot) -> ObservedCandle:
    """Reuse the existing Phase 4/5 observation; no second candle source exists."""

    return frame.liquidity.context.observation


def _optional_positive(value: Decimal | None, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise AnalysisInputError(f"{name} must be a positive, finite Decimal or None")


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    """One closed-candle supporting-indicator frame over existing displacement.

    Values are None during each indicator's exact causal warm-up. They are
    descriptive context for visualization and review only: no indicator
    generates, gates, or vetoes signals, and none is read by any decision
    module. ATR is the Phase 5 published value reused verbatim.
    """

    settings: IndicatorsConfig
    upstream: DisplacementSnapshot
    ema_values: tuple[Decimal | None, ...]
    rsi: Decimal | None
    atr: Decimal | None
    volume: Decimal
    volume_average: Decimal | None
    volume_ratio: Decimal | None
    provenance: EvidenceProvenance
    candle_index: int = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, IndicatorsConfig) or not isinstance(
            self.upstream, DisplacementSnapshot
        ):
            raise AnalysisInputError(
                "indicator frame requires IndicatorsConfig and an existing DisplacementSnapshot"
            )
        if not isinstance(self.ema_values, tuple) or len(self.ema_values) != len(
            self.settings.ema_periods
        ):
            raise AnalysisInputError(
                "ema_values must hold exactly one entry per configured EMA period"
            )
        for value in self.ema_values:
            _optional_positive(value, "ema value")
        if self.rsi is not None and (
            not isinstance(self.rsi, Decimal)
            or not self.rsi.is_finite()
            or not Decimal(0) <= self.rsi <= Decimal(100)
        ):
            raise AnalysisInputError("rsi must be a finite Decimal between 0 and 100 or None")
        _optional_positive(self.atr, "atr")
        if not isinstance(self.volume, Decimal) or not self.volume.is_finite() or self.volume < 0:
            raise AnalysisInputError("volume must be a nonnegative, finite Decimal")
        _optional_positive(self.volume_average, "volume_average")
        if (self.volume_average is None) != (self.volume_ratio is None):
            raise AnalysisInputError("volume_ratio exists exactly once volume_average exists")
        if self.volume_ratio is not None and (
            not isinstance(self.volume_ratio, Decimal)
            or not self.volume_ratio.is_finite()
            or self.volume_ratio <= 0
        ):
            raise AnalysisInputError("volume_ratio must be a positive, finite Decimal")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise AnalysisInputError("indicator frame requires provenance")
        if self.provenance.producer != "indicator-frame":
            raise AnalysisInputError("indicator frame producer must be indicator-frame")
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("indicator frame must reuse the current consumed prefix")
        if self.provenance.series != self.upstream.provenance.series:
            raise AnalysisInputError("indicator frame series must be the upstream series")
        observation = current_observation(self.upstream)
        _metadata(
            self.provenance,
            observation,
            (self.upstream.provenance.as_reference(),),
        )
        object.__setattr__(self, "candle_index", observation.reference.candle_index)
