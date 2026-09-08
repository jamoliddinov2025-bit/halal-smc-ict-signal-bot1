"""Interior range-overlap geometry over existing OB zones; no second detector."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.breaker_blocks.calculation import validate_frames as validate_mss_pair
from smcsignal.analysis.breaker_blocks.models import BreakerSnapshot
from smcsignal.analysis.displacement.calculation import difference
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.models import ObservedCandle
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.data import OHLCV


class MitigationDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


def direction_for(origin: OrderBlockEvent) -> MitigationDirection:
    return (
        MitigationDirection.BULLISH
        if origin.direction == TrendDirection.BULLISH
        else MitigationDirection.BEARISH
    )


def observation(frame: BreakerSnapshot) -> ObservedCandle:
    return frame.upstream.upstream.observation


def published_order_blocks(frame: BreakerSnapshot) -> tuple[OrderBlockEvent, ...]:
    return frame.upstream.upstream.upstream.events


def interior_overlap(low: Decimal, high: Decimal, zone_low: Decimal, zone_high: Decimal) -> bool:
    """Closed [low, high] intersects the open interval (zone_low, zone_high)."""
    return high > zone_low and low < zone_high


def intersects(origin: OrderBlockEvent, candle: OHLCV) -> bool:
    """Positive interior overlap: endpoint-only touches and total misses are excluded."""
    return interior_overlap(
        candle.low, candle.high, origin.zone_lower_boundary, origin.zone_upper_boundary
    )


def overlap_span(
    low: Decimal, high: Decimal, zone_low: Decimal, zone_high: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    lower, upper = max(low, zone_low), min(high, zone_high)
    size = difference(upper, lower) if upper > lower else Decimal(0)
    return lower, upper, size


def known_at_open(origin: OrderBlockEvent, current: ObservedCandle) -> bool:
    return (
        origin.confirmation_index < current.reference.candle_index
        and origin.available_at <= current.reference.opened_at
    )


def converted_ids(frame: BreakerSnapshot) -> frozenset[str]:
    return frozenset(block.original_ob_id for block in frame.events)


def validate_frames(previous: BreakerSnapshot | None, current: BreakerSnapshot) -> None:
    if not isinstance(current, BreakerSnapshot) or (
        previous is not None and not isinstance(previous, BreakerSnapshot)
    ):
        raise AnalysisInputError("Mitigation requires original BreakerSnapshot frames")
    validate_mss_pair(
        previous.upstream if previous is not None else None,
        current.upstream,
    )
    if previous is not None and (
        previous.settings != current.settings
        or previous.provenance.configuration_hash != current.provenance.configuration_hash
    ):
        raise AnalysisInputError(
            "upstream Breaker configuration cannot change during Mitigation replay"
        )
