"""Small deterministic test constructors, not trading data or strategy code."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smcsignal.analysis import Swing, SwingKind
from smcsignal.data import OHLCV

BASE = datetime(2024, 1, 1, tzinfo=UTC)
STEP = timedelta(minutes=15)
GOLDEN_PRICES = [10, 15, 12, 18, 14, 16, 20, 17, 12, 16, 10, 14, 8, 18]


def bar(index, close, *, high=None, low=None, opening=None, volume=1):
    close = Decimal(str(close))
    return OHLCV(
        timestamp=BASE + index * STEP,
        open=close if opening is None else Decimal(str(opening)),
        high=close + 1 if high is None else Decimal(str(high)),
        low=close - 1 if low is None else Decimal(str(low)),
        close=close,
        volume=Decimal(str(volume)),
    )


def series(prices):
    return tuple(bar(index, price) for index, price in enumerate(prices))


def mirrored(candles):
    ceiling = Decimal("1000")
    return tuple(
        OHLCV(
            timestamp=c.timestamp,
            open=ceiling - c.open,
            high=ceiling - c.low,
            low=ceiling - c.high,
            close=ceiling - c.close,
            volume=c.volume,
        )
        for c in candles
    )


def swing(kind: SwingKind, price, pivot_index, lag=1):
    return Swing(
        kind=kind,
        pivot_index=pivot_index,
        pivot_timestamp=BASE + pivot_index * STEP,
        price=Decimal(str(price)),
        confirmed_index=pivot_index + lag,
        confirmed_timestamp=BASE + (pivot_index + lag) * STEP,
    )
