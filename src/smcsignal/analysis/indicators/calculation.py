"""Deterministic indicator arithmetic. Context only; no thresholds, no gates."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from smcsignal.analysis.errors import AnalysisInputError

PRECISION = 50


def _context(precision: int = PRECISION) -> Context:
    return Context(prec=precision, rounding=ROUND_HALF_EVEN)


def _finite(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AnalysisInputError(f"indicator arithmetic requires a finite Decimal {name}")
    return value


def average(values: tuple[Decimal, ...]) -> Decimal:
    """Descriptive 50-digit mean of a nonempty window."""

    for value in values:
        _finite(value, "window value")
    if not values:
        raise AnalysisInputError("an average requires at least one value")
    with localcontext(_context()):
        return sum(values, Decimal(0)) / Decimal(len(values))


def ema_seed(closes: tuple[Decimal, ...]) -> Decimal:
    """Seed an EMA with the mean of its first period closes."""

    if not closes:
        raise AnalysisInputError("an EMA seed requires at least one close")
    return average(closes)


def ema_update(prior: Decimal, close: Decimal, period: int) -> Decimal:
    """Standard EMA step with multiplier 2/(period+1); deterministic 50 digits."""

    _finite(prior, "prior EMA")
    _finite(close, "close")
    if type(period) is not int or period < 1:
        raise AnalysisInputError("EMA period must be a positive integer")
    with localcontext(_context()):
        multiplier = Decimal(2) / Decimal(period + 1)
        return prior + (close - prior) * multiplier


def wilder_update(prior_average: Decimal, current: Decimal, period: int) -> Decimal:
    """Wilder smoothing step: (prior*(period-1) + current)/period."""

    _finite(prior_average, "prior average")
    _finite(current, "current change")
    if type(period) is not int or period < 1:
        raise AnalysisInputError("Wilder period must be a positive integer")
    with localcontext(_context()):
        return (prior_average * Decimal(period - 1) + current) / Decimal(period)


def rsi_from_averages(average_gain: Decimal, average_loss: Decimal) -> Decimal:
    """RSI from Wilder averages; flat windows are the documented neutral 50."""

    _finite(average_gain, "average gain")
    _finite(average_loss, "average loss")
    if average_gain < 0 or average_loss < 0:
        raise AnalysisInputError("Wilder averages must be nonnegative")
    if average_gain == 0 and average_loss == 0:
        return Decimal("50")
    if average_loss == 0:
        return Decimal(100)
    if average_gain == 0:
        return Decimal(0)
    with localcontext(_context()):
        return Decimal(100) * average_gain / (average_gain + average_loss)


def volume_ratio(volume: Decimal, volume_average: Decimal) -> Decimal:
    """Descriptive volume/average ratio; never a confirmation gate."""

    _finite(volume, "volume")
    _finite(volume_average, "volume average")
    if volume_average <= 0:
        raise AnalysisInputError("volume average must be positive")
    with localcontext(_context()):
        return volume / volume_average


class EMACalculator:
    """Fixed-origin EMA state for one period; pure function of consumed closes.

    Instances are immutable: update() returns the next state together with the
    value, so a caller can commit state only after everything else succeeded.
    """

    def __init__(self, period: int) -> None:
        if type(period) is not int or period < 1:
            raise AnalysisInputError("EMA period must be a positive integer")
        self._period = period
        self._seed_closes: tuple[Decimal, ...] = ()
        self._value: Decimal | None = None

    @property
    def period(self) -> int:
        return self._period

    @property
    def value(self) -> Decimal | None:
        return self._value

    def update(self, close: Decimal) -> tuple[EMACalculator, Decimal | None]:
        """Return (next state, EMA value) after this close; None during warm-up."""

        _finite(close, "close")
        if self._value is None:
            closes = (*self._seed_closes, close)
            if len(closes) < self._period:
                next_state = EMACalculator(self._period)
                next_state._seed_closes = closes
                return next_state, None
            value = ema_seed(closes)
            next_state = EMACalculator(self._period)
            next_state._value = value
            return next_state, value
        next_state = EMACalculator(self._period)
        next_state._value = ema_update(self._value, close, self._period)
        return next_state, next_state._value


class RSICalculator:
    """Fixed-origin Wilder RSI state; pure function of consumed closes.

    The first value is published after exactly `period` price changes
    (candle index `period`), seeded with the mean gain and mean loss.
    """

    def __init__(self, period: int) -> None:
        if type(period) is not int or period < 1:
            raise AnalysisInputError("RSI period must be a positive integer")
        self._period = period
        self._prior_close: Decimal | None = None
        self._gains: tuple[Decimal, ...] = ()
        self._losses: tuple[Decimal, ...] = ()
        self._average_gain: Decimal | None = None
        self._average_loss: Decimal | None = None

    @property
    def period(self) -> int:
        return self._period

    @property
    def value(self) -> Decimal | None:
        if self._average_gain is None or self._average_loss is None:
            return None
        return rsi_from_averages(self._average_gain, self._average_loss)

    def update(self, close: Decimal) -> tuple[RSICalculator, Decimal | None]:
        """Return (next state, RSI value) after this close; None during warm-up."""

        _finite(close, "close")
        next_state = RSICalculator(self._period)
        if self._prior_close is None:
            next_state._prior_close = close
            return next_state, None
        change = close - self._prior_close
        next_state._prior_close = close
        gain = change if change > 0 else Decimal(0)
        loss = -change if change < 0 else Decimal(0)
        if self._average_gain is None or self._average_loss is None:
            gains = (*self._gains, gain)
            losses = (*self._losses, loss)
            if len(gains) < self._period:
                next_state._gains = gains
                next_state._losses = losses
                return next_state, None
            average_gain = average(gains)
            average_loss = average(losses)
            next_state._average_gain = average_gain
            next_state._average_loss = average_loss
            return next_state, rsi_from_averages(average_gain, average_loss)
        average_gain = wilder_update(self._average_gain, gain, self._period)
        average_loss = wilder_update(self._average_loss, loss, self._period)
        next_state._average_gain = average_gain
        next_state._average_loss = average_loss
        return next_state, rsi_from_averages(average_gain, average_loss)


class VolumeAverageCalculator:
    """Fixed-origin rolling volume mean including the current candle."""

    def __init__(self, period: int) -> None:
        if type(period) is not int or period < 1:
            raise AnalysisInputError("volume average period must be a positive integer")
        self._period = period
        self._window: tuple[Decimal, ...] = ()

    @property
    def period(self) -> int:
        return self._period

    def update(
        self, volume: Decimal
    ) -> tuple[VolumeAverageCalculator, Decimal | None, Decimal | None]:
        """Return (next state, average, ratio); (None, None) during warm-up."""

        _finite(volume, "volume")
        window = (*self._window, volume)[-self._period :]
        next_state = VolumeAverageCalculator(self._period)
        next_state._window = window
        if len(window) < self._period:
            return next_state, None, None
        value = average(window)
        return next_state, value, volume_ratio(volume, value)
