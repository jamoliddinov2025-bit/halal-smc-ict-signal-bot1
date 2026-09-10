"""Immutable OTE zones and close observations against already-known ranges."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.displacement.calculation import difference
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.models import ObservedCandle, _metadata
from smcsignal.analysis.models import _price
from smcsignal.analysis.ote.calculation import (
    OTEClassification,
    OTEDirection,
    classify_price,
    direction_for,
    known_at_open,
    zone_geometry,
)
from smcsignal.analysis.ote.config import METHODOLOGY_VERSION, OTEConfig, PriceBasis
from smcsignal.analysis.premium_discount.models import DealingRange, PDSnapshot
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference


@dataclass(frozen=True, slots=True)
class OTEZone:
    """Fibonacci-style retracement interval of one confirmed Phase 8 dealing range."""

    dealing_range: DealingRange
    settings: OTEConfig
    provenance: EvidenceProvenance
    zone_id: str = field(init=False)
    range_id: str = field(init=False)
    direction: OTEDirection = field(init=False)
    price_unit: str = field(init=False)
    range_low: Decimal = field(init=False)
    range_high: Decimal = field(init=False)
    range_size: Decimal = field(init=False)
    lower_retracement_price: Decimal = field(init=False)
    upper_retracement_price: Decimal = field(init=False)
    lower_boundary: Decimal = field(init=False)
    upper_boundary: Decimal = field(init=False)
    zone_size: Decimal = field(init=False)
    confirmation_index: int = field(init=False)
    confirmation_timestamp: datetime = field(init=False)
    available_at: datetime = field(init=False)
    methodology_version: str = field(init=False, default=METHODOLOGY_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.dealing_range, DealingRange) or not isinstance(
            self.settings, OTEConfig
        ):
            raise AnalysisInputError("OTE zone requires a confirmed dealing range and OTE settings")
        source = self.dealing_range
        _metadata(self.provenance, source.context.observation, (source.provenance.as_reference(),))
        if (
            self.provenance.source_candles != source.provenance.source_candles
            or self.provenance.input_prefix_hash != source.provenance.input_prefix_hash
        ):
            raise AnalysisInputError("OTE zone must retain its dealing-range sources and prefix")
        lower_r, upper_r, lower, upper = zone_geometry(
            source.lower_boundary,
            source.upper_boundary,
            self.settings.lower_retracement,
            self.settings.upper_retracement,
            direction_for(source),
        )
        for name, value in (
            ("zone_id", self.provenance.evidence_id),
            ("range_id", source.range_id),
            ("direction", direction_for(source)),
            ("price_unit", source.price_unit),
            ("range_low", source.lower_boundary),
            ("range_high", source.upper_boundary),
            ("range_size", source.size),
            ("lower_retracement_price", lower_r),
            ("upper_retracement_price", upper_r),
            ("lower_boundary", lower),
            ("upper_boundary", upper),
            ("zone_size", difference(upper, lower)),
            ("confirmation_index", source.confirmation_index),
            ("confirmation_timestamp", source.confirmation_timestamp),
            ("available_at", source.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class OTEObservation:
    """Classification of one completed candle against an already-known OTE zone."""

    settings: OTEConfig
    zone: OTEZone | None
    evaluation: ObservedCandle
    provenance: EvidenceProvenance
    observation_id: str = field(init=False)
    zone_id: str | None = field(init=False)
    zone_reference: EvidenceReference | None = field(init=False)
    direction: OTEDirection | None = field(init=False)
    evaluated_price: Decimal = field(init=False)
    classification: OTEClassification = field(init=False)
    available_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, OTEConfig) or not isinstance(
            self.evaluation, ObservedCandle
        ):
            raise AnalysisInputError("OTE observation requires typed settings and a closed candle")
        if self.settings.price_basis is not PriceBasis.CLOSE:
            raise AnalysisInputError("OTE-v1 evaluates only the completed candle close")
        zone = self.zone
        if zone is not None:
            if not isinstance(zone, OTEZone):
                raise AnalysisInputError("OTE observation zone must be an OTEZone")
            current = self.evaluation
            if (
                zone.provenance.series != current.reference.series
                or not known_at_open(zone.dealing_range, current)
                or zone.available_at > current.available_at
            ):
                raise AnalysisInputError(
                    "OTE observation cannot use a range unknown before this candle opened"
                )
            required: tuple[EvidenceReference, ...] = (zone.provenance.as_reference(),)
            bounds: tuple[Decimal, Decimal] | None = (zone.lower_boundary, zone.upper_boundary)
        else:
            required = ()
            bounds = None
        _metadata(self.provenance, self.evaluation, required)
        if self.evaluation.reference not in self.provenance.source_candles:
            raise AnalysisInputError("OTE observation must retain the evaluated candle")
        price = self.evaluation.candle.close
        _price(price, "evaluated price")
        classification = classify_price(price, bounds, self.settings.boundary_policy)
        if (zone is None) != (classification is OTEClassification.INSUFFICIENT_CONTEXT):
            raise AnalysisInputError("insufficient OTE context must be explicit, never implied")
        for name, value in (
            ("observation_id", self.provenance.evidence_id),
            ("zone_id", None if zone is None else zone.zone_id),
            ("zone_reference", None if zone is None else zone.provenance.as_reference()),
            ("direction", None if zone is None else zone.direction),
            ("evaluated_price", price),
            ("classification", classification),
            ("available_at", self.evaluation.available_at),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class OTESnapshot:
    settings: OTEConfig
    upstream: PDSnapshot
    zone: OTEZone | None
    observation: OTEObservation
    provenance: EvidenceProvenance
    classification: OTEClassification = field(init=False)
    evaluated_price: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, OTEConfig) or not isinstance(self.upstream, PDSnapshot):
            raise AnalysisInputError("OTE snapshot requires settings and an existing PD frame")
        dealing_range = self.upstream.dealing_range
        if dealing_range is None:
            if self.zone is not None:
                raise AnalysisInputError("OTE cannot invent a zone without a current dealing range")
        else:
            if (
                not isinstance(self.zone, OTEZone)
                or self.zone.dealing_range is not dealing_range
                or self.zone.settings != self.settings
            ):
                raise AnalysisInputError(
                    "OTE must derive its current zone from this exact Phase 8 dealing range"
                )
        if not isinstance(self.observation, OTEObservation):
            raise AnalysisInputError("OTE snapshot requires an immutable observation")
        item = self.observation
        current = self.upstream.observation
        if item.settings != self.settings or item.evaluation != current:
            raise AnalysisInputError("OTE observation must belong to this exact evaluation candle")
        usable = (
            self.zone
            if self.zone is not None and known_at_open(self.zone.dealing_range, current)
            else None
        )
        if item.zone is not usable:
            raise AnalysisInputError(
                "OTE may classify a close only against a range known before the bar opened"
            )
        refs = [self.upstream.provenance.as_reference(), item.provenance.as_reference()]
        if self.zone is not None:
            refs.append(self.zone.provenance.as_reference())
        _metadata(self.provenance, current, tuple(refs))
        if self.provenance.input_prefix_hash != self.upstream.provenance.input_prefix_hash:
            raise AnalysisInputError("OTE snapshot must reuse the current consumed prefix")
        for name, value in (
            ("classification", item.classification),
            ("evaluated_price", item.evaluated_price),
        ):
            object.__setattr__(self, name, value)
