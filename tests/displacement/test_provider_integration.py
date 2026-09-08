from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from smcsignal.analysis import load_analysis_config
from smcsignal.analysis.displacement import (
    DisplacementAnalyzer,
    analyze_displacement,
    load_displacement_config,
)
from smcsignal.analysis.liquidity import LiquidityAnalyzer, analyze_liquidity, load_liquidity_config
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.displacement.helpers import SERIES, events, golden, settings, upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "displacement.example.toml"


def test_documented_default_csv_example_fetch_replay_and_stream_are_identical():
    data = load_data_config(PATH)
    config = load_displacement_config(PATH)
    liquidity = load_liquidity_config(PATH)
    analysis = load_analysis_config(PATH)
    provider = CsvDataProvider(data)
    assert provider.fetch_ohlcv().candles == golden()
    upstream_frames = analyze_liquidity(
        provider.fetch_ohlcv().candles, series=SERIES, config=liquidity, analysis_config=analysis
    )
    expected = analyze_displacement(upstream_frames, config, price_unit=liquidity.price_unit)
    source = LiquidityAnalyzer(series=SERIES, config=liquidity, analysis_config=analysis)
    detector = DisplacementAnalyzer(config, price_unit=liquidity.price_unit)
    actual = tuple(detector.update(source.update(c)) for c in provider.replay())
    assert expected == actual
    assert [(e.detection_index, e.direction.value) for e in events(actual)] == [
        (15, "bullish"),
        (16, "bearish"),
    ]


def test_mocked_binance_canonical_data_feeds_existing_pipeline_without_new_transport():
    candles = golden()
    rows = []
    for candle in candles:
        opening = (candle.timestamp - EPOCH) // timedelta(milliseconds=1)
        rows.append(
            [
                opening,
                str(candle.open),
                str(candle.high),
                str(candle.low),
                str(candle.close),
                str(candle.volume),
                opening + 899999,
                "0",
                0,
                "0",
                "0",
                "0",
            ]
        )
    provider = BinancePublicDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT", timeframe="15m", data_source="binance_public", history_limit=100
        ),
        transport=lambda url, timeout: rows,
        clock=lambda: candles[-1].timestamp + timedelta(minutes=15),
    )
    canonical = provider.fetch_ohlcv().candles
    assert canonical == candles
    declared = replace(SERIES, provider="binance_public", venue="mock_binance_spot")
    from_provider = analyze_displacement(
        upstream(canonical, series=declared), settings(atr_period=14), price_unit="USDT"
    )
    direct = analyze_displacement(
        upstream(candles, series=declared), settings(atr_period=14), price_unit="USDT"
    )
    assert from_provider == direct
    assert [e.detection_index for e in events(from_provider)] == [15, 16]
