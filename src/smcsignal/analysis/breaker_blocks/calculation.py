"""First closing violation of existing OB zones plus exact existing confirmation."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.mss.calculation import source_frame, validate_pair
from smcsignal.analysis.mss.models import MSSSnapshot
from smcsignal.analysis.order_blocks.models import OrderBlockEvent


class BreakerDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class BreakerRejection(StrEnum):
    SOURCE_NOT_KNOWN_AT_OPEN = "source_not_known_at_open"
    PREVIOUS_CLOSE_ALREADY_BEYOND = "previous_close_already_beyond"
    MISSING_DISPLACEMENT = "missing_displacement"
    WRONG_DISPLACEMENT_DIRECTION = "wrong_displacement_direction"
    MISSING_MSS = "missing_mss"
    WRONG_MSS_DIRECTION = "wrong_mss_direction"


def direction_for(origin: OrderBlockEvent) -> BreakerDirection:
    return (
        BreakerDirection.BULLISH
        if origin.direction == TrendDirection.BEARISH
        else BreakerDirection.BEARISH
    )


def violates(origin: OrderBlockEvent, close: Decimal) -> bool:
    """Strict opposing close, never wick-only or equality; no rounding or epsilon."""
    return (
        close > origin.zone_upper_boundary
        if origin.direction == TrendDirection.BEARISH
        else close < origin.zone_lower_boundary
    )


def validate_frames(previous: MSSSnapshot | None, current: MSSSnapshot) -> None:
    if not isinstance(current, MSSSnapshot) or (
        previous is not None and not isinstance(previous, MSSSnapshot)
    ):
        raise AnalysisInputError("Breaker requires original MSSSnapshot frames")
    validate_pair(previous.upstream if previous is not None else None, current.upstream)
    if previous is not None and (
        previous.settings != current.settings
        or previous.provenance.configuration_hash != current.provenance.configuration_hash
    ):
        raise AnalysisInputError("upstream MSS configuration cannot change during Breaker replay")


def rejection_reason(
    origin: OrderBlockEvent, previous: MSSSnapshot | None, current: MSSSnapshot
) -> BreakerRejection | None:
    observation = current.upstream.observation
    if (
        origin.confirmation_index >= observation.reference.candle_index
        or origin.available_at > observation.reference.opened_at
    ):
        return BreakerRejection.SOURCE_NOT_KNOWN_AT_OPEN
    if previous is None or violates(origin, previous.upstream.observation.candle.close):
        return BreakerRejection.PREVIOUS_CLOSE_ALREADY_BEYOND
    source = source_frame(current.upstream)
    if not source.events:
        return BreakerRejection.MISSING_DISPLACEMENT
    displacement = source.events[0]
    expected = direction_for(origin)
    if displacement.direction.value != expected.value:
        return BreakerRejection.WRONG_DISPLACEMENT_DIRECTION
    if not current.events:
        return BreakerRejection.MISSING_MSS
    mss = current.events[0]
    if mss.direction.value != expected.value:
        return BreakerRejection.WRONG_MSS_DIRECTION
    if (
        mss.evidence.displacement != displacement
        or mss.detection_index != observation.reference.candle_index
    ):
        raise AnalysisInputError("MSS must confirm this exact current displacement")
    if max(displacement.available_at, mss.available_at) > observation.available_at:
        raise AnalysisInputError("Breaker cannot use confirmation that is not yet available")
    return None
