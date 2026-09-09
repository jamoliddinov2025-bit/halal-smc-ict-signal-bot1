"""Causal, deterministic, exact-Decimal market regime annotations.

The classifier is a research annotation layer only. It consumes closed
candles one at a time, computes two exact Decimal metrics from the trailing
window, and labels the candle's regime with a fixed precedence. It never
generates, gates, or vetoes signals, never alters Phase 20 outcomes, and
never reads a candle beyond the one being observed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from decimal import Decimal

from smcsignal.analysis.displacement.calculation import ratio
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.robustness.config import RobustnessConfig
from smcsignal.analysis.robustness.models import MarketRegime, RegimeObservation
from smcsignal.data.models import OHLCV


def efficiency_ratio(closes: tuple[Decimal, ...], lookback_bars: int) -> Decimal:
    """Net absolute close move over the lookback, divided by the path length.

    The ratio lies in [0, 1]: one when every close-to-close move points the
    same way, zero when the net move is zero. A zero path length (an entirely
    flat window) yields exactly zero. All arithmetic is exact Decimal.
    """

    if type(lookback_bars) is not int or lookback_bars < 1:
        raise AnalysisInputError("the regime lookback must be a positive integer")
    if len(closes) < lookback_bars + 1:
        raise AnalysisInputError("the efficiency ratio requires a full lookback of closes")
    net = abs(closes[-1] - closes[-1 - lookback_bars])
    path = sum(
        (
            abs(closes[index] - closes[index - 1])
            for index in range(len(closes) - lookback_bars, len(closes))
        ),
        Decimal(0),
    )
    if path == 0:
        return Decimal(0)
    return ratio(net, path)


def volatility_ratio(
    closes: tuple[Decimal, ...], lookback_bars: int, baseline_bars: int
) -> Decimal | None:
    """Recent mean absolute close change over the baseline mean.

    ``None`` marks a degenerate flat baseline (zero summed change over the
    baseline window), where the ratio is mathematically undefined. The
    baseline window includes the recent lookback; both windows end at the
    observed candle. All arithmetic is exact Decimal.
    """

    if type(lookback_bars) is not int or type(baseline_bars) is not int:
        raise AnalysisInputError("the volatility windows must be positive integers")
    if lookback_bars < 1 or baseline_bars < lookback_bars:
        raise AnalysisInputError("the baseline window must be at least the lookback")
    if len(closes) < baseline_bars + 1:
        raise AnalysisInputError("the volatility ratio requires a full baseline of closes")

    def _mean_change(first: int, last: int, bars: int) -> Decimal:
        total = sum(
            (abs(closes[index] - closes[index - 1]) for index in range(first, last)), Decimal(0)
        )
        return ratio(total, Decimal(bars))

    baseline = _mean_change(len(closes) - baseline_bars, len(closes), baseline_bars)
    if baseline == 0:
        return None
    recent = _mean_change(len(closes) - lookback_bars, len(closes), lookback_bars)
    return ratio(recent, baseline)


def classify_regime(
    efficiency: Decimal | None, volatility: Decimal | None, config: RobustnessConfig
) -> MarketRegime | None:
    """Fixed-precedence regime label; ``None`` while metrics are unavailable.

    Precedence: TRENDING when the efficiency ratio reaches the trend
    threshold; otherwise HIGH_VOLATILITY or LOW_VOLATILITY when the
    volatility ratio crosses its threshold; otherwise RANGING. An undefined
    volatility ratio (flat baseline) falls through to RANGING unless the
    efficiency ratio already declared a trend.
    """

    if not isinstance(config, RobustnessConfig):
        raise AnalysisConfigurationError("config must be RobustnessConfig")
    if efficiency is None:
        return None
    if not isinstance(efficiency, Decimal) or not efficiency.is_finite():
        raise AnalysisInputError("the efficiency ratio must be a finite Decimal")
    if efficiency >= config.trend_threshold:
        return MarketRegime.TRENDING
    if volatility is not None:
        if not isinstance(volatility, Decimal) or not volatility.is_finite():
            raise AnalysisInputError("the volatility ratio must be a finite Decimal")
        if volatility >= config.high_volatility_threshold:
            return MarketRegime.HIGH_VOLATILITY
        if volatility <= config.low_volatility_threshold:
            return MarketRegime.LOW_VOLATILITY
    return MarketRegime.RANGING


class RegimeAnalyzer:
    """One explicit configuration per closed-candle replay; no I/O, no lookahead.

    ``update`` consumes one closed candle and returns that candle's causal
    regime observation. Observations before the baseline window fills carry
    ``None`` regime and metrics (warmup). The analyzer holds only the
    trailing baseline window of closes, so an observation never depends on a
    candle after its own index.
    """

    def __init__(self, config: RobustnessConfig | None = None) -> None:
        if config is not None and not isinstance(config, RobustnessConfig):
            raise AnalysisConfigurationError("config must be RobustnessConfig")
        self._config = config if config is not None else RobustnessConfig()
        self._closes: deque[Decimal] = deque(maxlen=self._config.regime_baseline_bars + 1)
        self._count = 0
        self._latest: RegimeObservation | None = None

    @property
    def config(self) -> RobustnessConfig:
        return self._config

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def latest(self) -> RegimeObservation | None:
        return self._latest

    def update(self, candle: OHLCV) -> RegimeObservation:
        """Observe one closed candle; the regime at it uses no future data."""

        if not isinstance(candle, OHLCV):
            raise AnalysisInputError("regime observations require canonical OHLCV candles")
        self._closes.append(candle.close)
        index = self._count
        self._count += 1
        lookback = self._config.regime_lookback_bars
        baseline = self._config.regime_baseline_bars
        warmup = len(self._closes) < baseline + 1
        efficiency: Decimal | None = None
        volatility: Decimal | None = None
        regime: MarketRegime | None = None
        if not warmup:
            closes = tuple(self._closes)
            efficiency = efficiency_ratio(closes, lookback)
            volatility = volatility_ratio(closes, lookback, baseline)
            regime = classify_regime(efficiency, volatility, self._config)
        observation = RegimeObservation(
            index=index,
            opened_at=candle.timestamp,
            regime=regime,
            efficiency_ratio=efficiency,
            volatility_ratio=volatility,
            lookback_bars=lookback,
            baseline_bars=baseline,
        )
        self._latest = observation
        return observation


def analyze_regimes(
    candles: Iterable[OHLCV], config: RobustnessConfig | None = None
) -> tuple[RegimeObservation, ...]:
    """Sequential batch replay; identical to streaming updates."""

    analyzer = RegimeAnalyzer(config)
    try:
        iterator = iter(candles)
    except TypeError as exc:
        raise AnalysisInputError("regimes require an iterable of OHLCV candles") from exc
    return tuple(analyzer.update(candle) for candle in iterator)
