"""Synthetic Phase 21 fixtures; the pipeline and replay layers are never duplicated."""

from decimal import Decimal

from smcsignal.analysis.backtest import ReplayDataset
from smcsignal.analysis.robustness import RobustnessConfig
from tests.backtest.helpers import (
    DATASET_ID,
    EIGHT,
    GENERATED_AT,
    PRIMARY_PRICES,
    bar_at,
    bars,
    configuration,
    dataset,
    higher_candles,
)

__all__ = [
    "CHOP_ONLY",
    "DATASET_ID",
    "EIGHT",
    "GENERATED_AT",
    "PRIMARY_PRICES",
    "RISE_THEN_CHOP",
    "RISE_THEN_FALL",
    "bar_at",
    "bars",
    "configuration",
    "dataset",
    "higher_candles",
    "rich_dataset",
    "robustness",
]

# 30 rising candles followed by 30 choppy candles: three walk-forward windows
# whose validation segments cover STABLE, UNDERSAMPLED, and WEAK outcomes.
RISE_THEN_CHOP = (*range(20, 50), *(30 - (index % 5) for index in range(30)))
# 30 rising candles followed by 30 falling candles: fewer late publications.
RISE_THEN_FALL = (*range(20, 50), *range(50, 20, -1))
# A short choppy-only history that publishes no signals at all.
CHOP_ONLY = tuple(30 - (index % 4) for index in range(30))


def robustness(
    *,
    development_bars: int = 12,
    validation_bars: int = 14,
    step_bars: int = 14,
    regime_lookback_bars: int = 3,
    regime_baseline_multiple: int = 2,
    trend_threshold: str = "0.30",
    high_volatility_threshold: str = "1.50",
    low_volatility_threshold: str = "0.75",
    minimum_finalized_for_stability: int = 1,
    minimum_windows_for_stability: int = 2,
    stability_win_rate_floor: str = "0.50",
    maximum_win_rate_spread: str = "0.50",
) -> RobustnessConfig:
    """The hand-verified fixture configuration; three windows over 60 candles."""

    return RobustnessConfig(
        development_bars=development_bars,
        validation_bars=validation_bars,
        step_bars=step_bars,
        regime_lookback_bars=regime_lookback_bars,
        regime_baseline_multiple=regime_baseline_multiple,
        trend_threshold=Decimal(trend_threshold),
        high_volatility_threshold=Decimal(high_volatility_threshold),
        low_volatility_threshold=Decimal(low_volatility_threshold),
        minimum_finalized_for_stability=minimum_finalized_for_stability,
        minimum_windows_for_stability=minimum_windows_for_stability,
        stability_win_rate_floor=Decimal(stability_win_rate_floor),
        maximum_win_rate_spread=Decimal(maximum_win_rate_spread),
    )


def rich_dataset(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    prices=RISE_THEN_CHOP,
    dataset_id: str = DATASET_ID,
) -> ReplayDataset:
    """The hand-verified rich fixture: three windows, mixed statuses and regimes."""

    return ReplayDataset(
        symbol=symbol,
        timeframe=timeframe,
        candles=bars(timeframe, prices, start=EIGHT),
        higher_candles=higher_candles(),
        dataset_id=dataset_id,
    )
