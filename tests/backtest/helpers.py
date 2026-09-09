"""Synthetic Phase 20 replay fixtures; the pipeline itself is never duplicated."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smcsignal.analysis import AnalysisConfig
from smcsignal.analysis.backtest import (
    BacktestConfiguration,
    ReplayDataset,
    run_backtest,
)
from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.liquidity import LiquidityConfig
from smcsignal.analysis.mtf import timeframe_seconds
from smcsignal.analysis.outcome_tracking import OutcomeTrackingConfig
from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_engine import SignalEngineConfig
from smcsignal.data import OHLCV

MIDNIGHT = datetime(2024, 1, 1, tzinfo=UTC)
EIGHT = datetime(2024, 1, 1, 8, tzinfo=UTC)
HOUR_PRICES = (15, 10, 16, 20, 18, 12, 17, 22, 19, 18, 17, 16)
PRIMARY_PRICES = tuple(range(20, 37))
FOUR_HOUR_PRICES = (15,)
GENERATED_AT = datetime(2024, 2, 1, tzinfo=UTC)
DATASET_ID = "phase13-fixture:v1"  # matches tests.mtf helpers' series identity


def bar_at(opened_at, close, *, high=None, low=None, opening=None, volume=1):
    close = Decimal(str(close))
    return OHLCV(
        timestamp=opened_at,
        open=close if opening is None else Decimal(str(opening)),
        high=close + 1 if high is None else Decimal(str(high)),
        low=close - 1 if low is None else Decimal(str(low)),
        close=close,
        volume=Decimal(str(volume)),
    )


def bars(timeframe, prices, *, start=MIDNIGHT):
    step = timedelta(seconds=timeframe_seconds(timeframe))
    return tuple(bar_at(start + index * step, price) for index, price in enumerate(prices))


def configuration(*, threshold: int = 10, horizon: int = 10) -> BacktestConfiguration:
    """The hand-verified fixture pipeline; BUY facts at index i use candles 0..i."""

    return BacktestConfiguration(
        analysis=AnalysisConfig(3),
        liquidity=LiquidityConfig("USDT"),
        displacement=DisplacementConfig(atr_period=3),
        setup_quality=SetupQualityConfig(threshold),
        signal_engine=SignalEngineConfig(publish_threshold=threshold),
        outcome_tracking=OutcomeTrackingConfig(horizon_bars=horizon),
    )


def higher_candles():
    return {"1h": bars("1h", HOUR_PRICES), "4h": bars("4h", FOUR_HOUR_PRICES, start=EIGHT)}


def dataset(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    prices=PRIMARY_PRICES,
    start: datetime = EIGHT,
    higher=None,
    dataset_id: str = DATASET_ID,
) -> ReplayDataset:
    return ReplayDataset(
        symbol=symbol,
        timeframe=timeframe,
        candles=bars(timeframe, prices, start=start),
        higher_candles=higher_candles() if higher is None else higher,
        dataset_id=dataset_id,
    )


def report(datasets=None, config=None):
    return run_backtest(
        datasets if datasets is not None else [dataset()],
        config if config is not None else configuration(),
    )


def buy_frames(result):
    """Published BUY signal frames of one replay, in order."""

    return tuple(step.signal for step in result.steps if step.signal.status.value == "BUY_SIGNAL")
