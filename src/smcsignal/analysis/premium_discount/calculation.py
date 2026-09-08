"""Exact local-range geometry and publication-time PD array coordinates."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple, TypeAlias

from smcsignal.analysis.displacement.calculation import difference, exact_sum, product
from smcsignal.analysis.displacement.models import DisplacementEvent
from smcsignal.analysis.fvg.models import FVGEvent
from smcsignal.analysis.liquidity.models import LiquidityPool, SweepEvent, SwingEvidence
from smcsignal.analysis.models import _price
from smcsignal.analysis.order_blocks.models import OrderBlockEvent, OrderBlockSnapshot

HALF = Decimal("0.5")


class PDClassification(StrEnum):
    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"
    OUTSIDE_RANGE = "OUTSIDE_RANGE"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


class RangeStatus(StrEnum):
    CONFIRMED = "confirmed"
    MISSING_SWINGS = "missing_swings"
    SAME_PIVOT = "same_pivot"
    NONPOSITIVE_SPAN = "nonpositive_span"


class PDArrayKind(StrEnum):
    LIQUIDITY_POOL = "liquidity_pool"
    SWEEP = "sweep"
    DISPLACEMENT = "displacement"
    FVG = "fvg"
    ORDER_BLOCK = "order_block"


PDSubject: TypeAlias = LiquidityPool | SweepEvent | DisplacementEvent | FVGEvent | OrderBlockEvent


class ArrayPrices(NamedTuple):
    kind: PDArrayKind
    lower: Decimal
    upper: Decimal
    price: Decimal
    publication_index: int
    price_unit: str


def pair_status(low: SwingEvidence | None, high: SwingEvidence | None) -> RangeStatus:
    if low is None or high is None:
        return RangeStatus.MISSING_SWINGS
    if low.swing.pivot_index == high.swing.pivot_index:
        return RangeStatus.SAME_PIVOT
    if high.swing.price <= low.swing.price:
        return RangeStatus.NONPOSITIVE_SPAN
    return RangeStatus.CONFIRMED


def midpoint(lower: Decimal, upper: Decimal) -> Decimal:
    """No rounded division or binary floats; reuse the approved exact helpers."""
    return exact_sum((lower, product(difference(upper, lower), HALF)))


def equilibrium_band(
    lower: Decimal, upper: Decimal, fraction: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    center = midpoint(lower, upper)
    half_width = product(difference(upper, lower), fraction)
    return center, half_width, difference(center, half_width), exact_sum((center, half_width))


def classify_price(
    price: Decimal, bounds: tuple[Decimal, Decimal, Decimal, Decimal] | None
) -> PDClassification:
    _price(price, "evaluated price")
    if bounds is None:
        return PDClassification.INSUFFICIENT_CONTEXT
    lower, upper, eq_lower, eq_upper = bounds
    if price < lower or price > upper:
        return PDClassification.OUTSIDE_RANGE
    if eq_lower <= price <= eq_upper:
        return PDClassification.EQUILIBRIUM
    return PDClassification.DISCOUNT if price < eq_lower else PDClassification.PREMIUM


def array_prices(subject: PDSubject) -> ArrayPrices:
    if isinstance(subject, LiquidityPool):
        return ArrayPrices(
            PDArrayKind.LIQUIDITY_POOL,
            subject.lower_bound,
            subject.upper_bound,
            subject.reference_price,
            subject.context.snapshot.candle_index,
            subject.settings.price_unit,
        )
    if isinstance(subject, SweepEvent):
        return ArrayPrices(
            PDArrayKind.SWEEP,
            subject.extreme_price,
            subject.extreme_price,
            subject.extreme_price,
            subject.breach.reference.candle_index,
            subject.pool.settings.price_unit,
        )
    if isinstance(subject, DisplacementEvent):
        close = subject.observation.candle.close
        return ArrayPrices(
            PDArrayKind.DISPLACEMENT,
            close,
            close,
            close,
            subject.detection_index,
            subject.price_unit,
        )
    if isinstance(subject, FVGEvent):
        return ArrayPrices(
            PDArrayKind.FVG,
            subject.lower_boundary,
            subject.upper_boundary,
            midpoint(subject.lower_boundary, subject.upper_boundary),
            subject.detection_index,
            subject.price_unit,
        )
    return ArrayPrices(
        PDArrayKind.ORDER_BLOCK,
        subject.zone_lower_boundary,
        subject.zone_upper_boundary,
        midpoint(subject.zone_lower_boundary, subject.zone_upper_boundary),
        subject.confirmation_index,
        subject.price_unit,
    )


def published_arrays(frame: OrderBlockSnapshot) -> tuple[PDSubject, ...]:
    gap = frame.upstream
    displacement = gap.upstream
    liquidity = displacement.liquidity
    return (
        *liquidity.pool_updates,
        *liquidity.sweeps,
        *displacement.events,
        *gap.events,
        *frame.events,
    )
