"""Strict three-candle geometry over existing immutable Phase 5 evidence."""

from __future__ import annotations

from decimal import Decimal
from typing import TypeAlias

from smcsignal.analysis.displacement.calculation import difference
from smcsignal.analysis.displacement.models import DisplacementEvent, DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.config import FVGConfig
from smcsignal.analysis.models import TrendDirection
from smcsignal.data import OHLCV

GapValues: TypeAlias = tuple[TrendDirection, Decimal, Decimal, Decimal]


def validate_window(window: tuple[DisplacementSnapshot, ...]) -> None:
    if (
        not isinstance(window, tuple)
        or not 1 <= len(window) <= 3
        or not all(isinstance(f, DisplacementSnapshot) for f in window)
    ):
        raise AnalysisInputError(
            "FVG window must be an immutable tuple of one to three Phase 5 frames"
        )
    for previous, current in zip(window, window[1:], strict=False):
        old, new = previous.metrics.observation, current.metrics.observation
        old_context, new_context = previous.liquidity.context, current.liquidity.context
        if (
            old.reference.candle_index + 1 != new.reference.candle_index
            or old.reference.series != new.reference.series
            or previous.price_unit != current.price_unit
            or previous.settings != current.settings
            or previous.provenance.configuration_hash != current.provenance.configuration_hash
            or old_context.provenance.configuration_hash
            != new_context.provenance.configuration_hash
            or old_context.provenance.as_reference() not in new_context.provenance.dependencies
            or old.reference.closed_at > new.reference.opened_at
            or old.available_at > new.available_at
        ):
            raise AnalysisInputError(
                "FVG upstream history, series, settings, or availability is discontinuous"
            )


def gap_geometry(c1: OHLCV, c3: OHLCV) -> GapValues | None:
    """Equality is never a gap. C2 has no extra visual/body requirement here."""
    if not isinstance(c1, OHLCV) or not isinstance(c3, OHLCV):
        raise AnalysisInputError("gap geometry requires canonical outer OHLCV candles")
    if c3.low > c1.high:
        direction, lower, upper = TrendDirection.BULLISH, c1.high, c3.low
    elif c3.high < c1.low:
        direction, lower, upper = TrendDirection.BEARISH, c3.high, c1.low
    else:
        return None
    return direction, lower, upper, difference(upper, lower)


def middle_displacement(window: tuple[DisplacementSnapshot, ...]) -> DisplacementEvent | None:
    if len(window) != 3 or not window[1].events:
        return None
    return window[1].events[0]


def qualified_gap(window: tuple[DisplacementSnapshot, ...], config: FVGConfig) -> GapValues | None:
    if len(window) != 3:
        return None
    values = gap_geometry(
        window[0].metrics.observation.candle, window[2].metrics.observation.candle
    )
    if values is None or values[3] < config.min_gap_size:
        return None
    displacement = middle_displacement(window)
    if config.require_displacement and (
        displacement is None or displacement.direction != values[0]
    ):
        return None
    return values
