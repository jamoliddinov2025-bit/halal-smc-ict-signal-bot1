from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from smcsignal.analysis import load_analysis_config
from smcsignal.analysis.displacement import (
    DisplacementAnalyzer,
    analyze_displacement,
    load_displacement_config,
)
from smcsignal.analysis.fvg import FVGAnalyzer, analyze_fvg, load_fvg_config
from smcsignal.analysis.liquidity import LiquidityAnalyzer, analyze_liquidity, load_liquidity_config
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.fvg.helpers import SERIES, events, golden, upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "fvg.example.toml"


def test_documented_csv_with_all_default_layers_has_exact_fvg_events_and_stream_identity():
    data, liquidity, analysis, displacement, config = (
        load_data_config(PATH),
        load_liquidity_config(PATH),
        load_analysis_config(PATH),
        load_displacement_config(PATH),
        load_fvg_config(PATH),
    )
    provider = CsvDataProvider(data)
    assert provider.fetch_ohlcv().candles == golden()
    base = analyze_liquidity(
        provider.fetch_ohlcv().candles, series=SERIES, config=liquidity, analysis_config=analysis
    )
    parent = analyze_displacement(base, displacement, price_unit=liquidity.price_unit)
    expected = analyze_fvg(parent, config)
    source = LiquidityAnalyzer(series=SERIES, config=liquidity, analysis_config=analysis)
    middle = DisplacementAnalyzer(displacement, price_unit=liquidity.price_unit)
    detector = FVGAnalyzer(config)
    actual = tuple(detector.update(middle.update(source.update(c))) for c in provider.replay())
    assert expected == actual
    assert [
        (e.detection_index, e.direction.value, e.lower_boundary, e.upper_boundary)
        for e in events(actual)
    ] == [(16, "bullish", 101, 104), (19, "bearish", 98, 106)]
    assert all(e.associated_displacement is not None for e in events(actual))


def test_mocked_binance_data_uses_existing_providers_and_produces_the_same_geometry():
    candles = golden()
    payload = []
    for c in candles:
        opening = (c.timestamp - EPOCH) // timedelta(milliseconds=1)
        payload.append(
            [
                opening,
                str(c.open),
                str(c.high),
                str(c.low),
                str(c.close),
                str(c.volume),
                opening + 899999,
                "0",
                0,
                "0",
                "0",
                "0",
            ]
        )
    source = BinancePublicDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT", timeframe="15m", data_source="binance_public", history_limit=100
        ),
        transport=lambda url, timeout: payload,
        clock=lambda: candles[-1].timestamp + timedelta(minutes=15),
    )
    canonical = source.fetch_ohlcv().candles
    assert canonical == candles
    declared = replace(SERIES, venue="mock_binance_spot", provider="binance_public")
    assert analyze_fvg(upstream(canonical, series=declared)) == analyze_fvg(
        upstream(candles, series=declared)
    )
