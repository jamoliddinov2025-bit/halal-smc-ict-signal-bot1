"""Trend from the latest two confirmed highs and two confirmed lows only."""

from dataclasses import replace
from datetime import datetime

from smcsignal.analysis.models import Swing, TrendDirection, TrendState


def classify_trend(
    *,
    candle_index: int,
    timestamp: datetime,
    swing_highs: tuple[Swing, ...] = (),
    swing_lows: tuple[Swing, ...] = (),
) -> TrendState:
    """Reject unavailable evidence; classify strict HH+HL / LH+LL / other.

    Insufficient evidence is ranging with ready=False. Equality and mixed
    evidence are ranging with ready=True. A break does not override this rule.
    """
    state = TrendState(
        candle_index=candle_index,
        timestamp=timestamp,
        direction=TrendDirection.RANGING,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
    )
    if not state.ready:
        return state
    old_high, new_high = (swing.price for swing in state.swing_highs)
    old_low, new_low = (swing.price for swing in state.swing_lows)
    if new_high > old_high and new_low > old_low:
        return replace(state, direction=TrendDirection.BULLISH)
    if new_high < old_high and new_low < old_low:
        return replace(state, direction=TrendDirection.BEARISH)
    return state
