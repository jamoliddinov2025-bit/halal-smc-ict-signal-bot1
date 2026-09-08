"""Exact Fibonacci-style OTE geometry over an existing Phase 8 dealing range."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.displacement.calculation import difference, exact_sum, product
from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.calculation import validate_window
from smcsignal.analysis.liquidity.models import ObservedCandle
from smcsignal.analysis.models import TrendDirection, _price
from smcsignal.analysis.ote.config import BoundaryPolicy
from smcsignal.analysis.premium_discount.models import DealingRange, PDSnapshot


class OTEDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class OTEClassification(StrEnum):
    INSIDE_OTE = "INSIDE_OTE"
    BELOW_OTE = "BELOW_OTE"
    ABOVE_OTE = "ABOVE_OTE"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


def source_frame(frame: PDSnapshot) -> DisplacementSnapshot:
    return frame.upstream.upstream.upstream


def direction_for(dealing_range: DealingRange) -> OTEDirection:
    if dealing_range.direction == TrendDirection.BULLISH:
        return OTEDirection.BULLISH
    if dealing_range.direction == TrendDirection.BEARISH:
        return OTEDirection.BEARISH
    raise AnalysisInputError("OTE requires a bullish or bearish dealing range")


def retracement_price(
    low: Decimal, high: Decimal, ratio: Decimal, direction: OTEDirection
) -> Decimal:
    """Bullish: H - r*(H-L). Bearish: L + r*(H-L). Uses configured r, never 0.618/0.786."""
    _price(low, "range low")
    _price(high, "range high")
    if not isinstance(ratio, Decimal) or not ratio.is_finite():
        raise AnalysisInputError("retracement ratio must be a finite Decimal")
    offset = product(ratio, difference(high, low))
    if direction == OTEDirection.BULLISH:
        return difference(high, offset)
    if direction == OTEDirection.BEARISH:
        return exact_sum((low, offset))
    raise AnalysisInputError("OTE retracement requires a bullish or bearish direction")


def ordered_bounds(first: Decimal, second: Decimal) -> tuple[Decimal, Decimal]:
    return (first, second) if first <= second else (second, first)


def zone_geometry(
    low: Decimal,
    high: Decimal,
    lower_retracement: Decimal,
    upper_retracement: Decimal,
    direction: OTEDirection,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    lower_price = retracement_price(low, high, lower_retracement, direction)
    upper_price = retracement_price(low, high, upper_retracement, direction)
    lower, upper = ordered_bounds(lower_price, upper_price)
    return lower_price, upper_price, lower, upper


def classify_price(
    price: Decimal,
    bounds: tuple[Decimal, Decimal] | None,
    policy: BoundaryPolicy = BoundaryPolicy.INCLUSIVE,
) -> OTEClassification:
    _price(price, "evaluated price")
    if bounds is None:
        return OTEClassification.INSUFFICIENT_CONTEXT
    if policy is not BoundaryPolicy.INCLUSIVE:
        raise AnalysisInputError("OTE-v1 classifies only with inclusive boundaries")
    lower, upper = bounds
    if lower <= price <= upper:
        return OTEClassification.INSIDE_OTE
    return OTEClassification.BELOW_OTE if price < lower else OTEClassification.ABOVE_OTE


def known_at_open(dealing_range: DealingRange, current: ObservedCandle) -> bool:
    return (
        dealing_range.confirmation_index < current.reference.candle_index
        and dealing_range.available_at <= current.reference.opened_at
    )


def validate_frames(previous: PDSnapshot | None, current: PDSnapshot) -> None:
    if not isinstance(current, PDSnapshot):
        raise AnalysisInputError("OTE consumes existing PDSnapshot frames, not raw data")
    if previous is None:
        if current.observation.reference.candle_index != 0:
            raise AnalysisInputError("OTE history must start at observed index zero")
        return
    if not isinstance(previous, PDSnapshot):
        raise AnalysisInputError("OTE previous context must be an existing PD frame")
    validate_window((source_frame(previous), source_frame(current)))
    for old, new in (
        (previous, current),
        (previous.upstream, current.upstream),
        (previous.upstream.upstream, current.upstream.upstream),
    ):
        if (
            old.provenance.configuration_hash != new.provenance.configuration_hash
            or old.settings != new.settings
        ):
            raise AnalysisInputError(
                "upstream PD/OB/FVG configuration cannot change during OTE replay"
            )
