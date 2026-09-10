"""Incremental close-based BOS/CHoCH over previously confirmed swing levels."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.models import (
    AnalysisSnapshot,
    StructureEvent,
    StructureEventKind,
    Swing,
    SwingKind,
    TrendDirection,
)
from smcsignal.analysis.swings import SwingDetector, _candles
from smcsignal.analysis.trend import classify_trend
from smcsignal.data import OHLCV


@dataclass(slots=True)
class _Level:
    swing: Swing
    consumed: bool = False


class MarketStructureAnalyzer:
    """Closed-candle stream processor; no I/O, future access, or historical edits."""

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        self._detector = SwingDetector(config)
        self._highs: deque[Swing] = deque(maxlen=2)
        self._lows: deque[Swing] = deque(maxlen=2)
        self._active_high: _Level | None = None
        self._active_low: _Level | None = None
        self._previous_close: Decimal | None = None
        self._latest: AnalysisSnapshot | None = None

    @property
    def config(self) -> AnalysisConfig:
        return self._detector.config

    @property
    def latest(self) -> AnalysisSnapshot | None:
        return self._latest

    def update(self, candle: OHLCV) -> AnalysisSnapshot:
        """Validate, evaluate prior levels/trend, then publish new confirmations.

        A CHoCH is an opposing break warning, not an automatic trend reversal.
        Ranging-state crossings consume the level but have no BOS/CHoCH label.
        """
        confirmed = self._detector.update(candle)  # validates before mutating any stream state
        index = self._detector.processed_count - 1
        prior_trend = (
            self._latest.trend.direction if self._latest is not None else TrendDirection.RANGING
        )
        events = self._break_events(candle, index, prior_trend)
        # New confirmations must not retire prior levels before this candle's crossing check.
        for swing in confirmed:
            if swing.kind == SwingKind.HIGH:
                self._highs.append(swing)
                self._active_high = _Level(swing)
            else:
                self._lows.append(swing)
                self._active_low = _Level(swing)
        trend = classify_trend(
            candle_index=index,
            timestamp=candle.timestamp,
            swing_highs=tuple(self._highs),
            swing_lows=tuple(self._lows),
        )
        snapshot = AnalysisSnapshot(
            candle_index=index,
            timestamp=candle.timestamp,
            confirmed_swings=confirmed,
            trend=trend,
            events=events,
        )
        self._previous_close = candle.close
        self._latest = snapshot
        return snapshot

    def _break_events(
        self, candle: OHLCV, index: int, prior_trend: TrendDirection
    ) -> tuple[StructureEvent, ...]:
        if self._previous_close is None:
            return ()
        events = []
        for level in (self._active_high, self._active_low):
            if level is None or level.consumed:
                continue
            swing = level.swing
            if swing.kind == SwingKind.HIGH:
                crossed = self._previous_close <= swing.price < candle.close
                direction = TrendDirection.BULLISH
            else:
                crossed = self._previous_close >= swing.price > candle.close
                direction = TrendDirection.BEARISH
            if not crossed:
                continue
            level.consumed = True  # one opportunity per swing, including unclassified range breaks
            if prior_trend == TrendDirection.RANGING:
                continue
            kind = StructureEventKind.BOS if direction == prior_trend else StructureEventKind.CHOCH
            events.append(
                StructureEvent(
                    candle_index=index,
                    timestamp=candle.timestamp,
                    kind=kind,
                    direction=direction,
                    level=swing,
                    previous_close=self._previous_close,
                    close=candle.close,
                    trend_before=prior_trend,
                )
            )
        return tuple(events)


def analyze(
    candles: Iterable[OHLCV], config: AnalysisConfig | None = None
) -> tuple[AnalysisSnapshot, ...]:
    """Batch convenience API: exactly the same sequential updates as live replay.

    Use a fixed starting history/configuration when comparing prefixes. This
    helper does not sort inputs, inspect the end of a series, or flush tail pivots.
    """
    analyzer = MarketStructureAnalyzer(config)
    return tuple(analyzer.update(candle) for candle in _candles(candles))
