"""Causal symmetric fractals: delay publication until the right flank closes."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from datetime import datetime

from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.models import Swing, SwingKind
from smcsignal.data import OHLCV


def _candles(candles: Iterable[OHLCV]) -> Iterator[OHLCV]:
    try:
        return iter(candles)
    except TypeError as exc:
        raise AnalysisInputError("candles must be an iterable of canonical OHLCV records") from exc


class SwingDetector:
    """Process closed candles once, with O(fractal_length) rolling state."""

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        if config is not None and not isinstance(config, AnalysisConfig):
            raise AnalysisConfigurationError("config must be an AnalysisConfig")
        self._config = config if config is not None else AnalysisConfig()
        self._window: deque[tuple[int, OHLCV]] = deque(maxlen=self.config.fractal_length)
        self._processed_count = 0
        self._last_timestamp: datetime | None = None

    @property
    def config(self) -> AnalysisConfig:
        return self._config

    @property
    def processed_count(self) -> int:
        return self._processed_count

    def update(self, candle: OHLCV) -> tuple[Swing, ...]:
        """Emit only swings first confirmed by this candle, high before low.

        Strict extrema are required on both flanks. Ties invalidate that pivot
        kind. A single outside candle can independently qualify as high and low.
        Invalid or repeated timestamps are rejected before any state mutation.
        """
        if not isinstance(candle, OHLCV):
            raise AnalysisInputError("analysis requires canonical OHLCV records")
        if self._last_timestamp is not None and candle.timestamp <= self._last_timestamp:
            raise AnalysisInputError("candles must be strictly chronological and unique")
        index = self._processed_count
        self._window.append((index, candle))
        self._processed_count += 1
        self._last_timestamp = candle.timestamp
        if len(self._window) < self.config.fractal_length:
            return ()
        window = tuple(self._window)
        radius = self.config.confirmation_delay
        pivot_index, pivot = window[radius]
        others = [bar for position, (_, bar) in enumerate(window) if position != radius]
        confirmed = []
        for kind, qualifies, price in (
            (SwingKind.HIGH, all(pivot.high > bar.high for bar in others), pivot.high),
            (SwingKind.LOW, all(pivot.low < bar.low for bar in others), pivot.low),
        ):
            if qualifies:
                confirmed.append(
                    Swing(
                        kind=kind,
                        pivot_index=pivot_index,
                        pivot_timestamp=pivot.timestamp,
                        price=price,
                        confirmed_index=index,
                        confirmed_timestamp=candle.timestamp,
                    )
                )
        return tuple(confirmed)


def detect_swings(
    candles: Iterable[OHLCV], config: AnalysisConfig | None = None
) -> tuple[Swing, ...]:
    """Collect streaming confirmations, ordered by availability, never backdated."""
    detector = SwingDetector(config)
    return tuple(swing for candle in _candles(candles) for swing in detector.update(candle))
