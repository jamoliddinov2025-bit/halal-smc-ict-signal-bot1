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
from smcsignal.analysis.mss import MSSAnalyzer, analyze_mss, load_mss_config
from smcsignal.analysis.order_blocks import (
    OrderBlockAnalyzer,
    analyze_order_blocks,
    load_order_block_config,
)
from smcsignal.analysis.premium_discount import PDAnalyzer, analyze_pd, load_pd_config
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.mss.helpers import SERIES, events, golden, upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "mss.example.toml"


def test_documented_csv_pipeline_batch_and_stream_are_identical():
    data, analysis, liq, disp, fvg, ob, pd, mss = (
        load_data_config(PATH),
        load_analysis_config(PATH),
        load_liquidity_config(PATH),
        load_displacement_config(PATH),
        load_fvg_config(PATH),
        load_order_block_config(PATH),
        load_pd_config(PATH),
        load_mss_config(PATH),
    )
    provider = CsvDataProvider(data)
    candles = provider.fetch_ohlcv().candles
    assert candles == golden()
    a = analyze_liquidity(candles, series=SERIES, config=liq, analysis_config=analysis)
    b = analyze_displacement(a, disp, price_unit=liq.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, fvg), ob)
    expected = analyze_mss(analyze_pd(c, pd), mss)
    liquidity = LiquidityAnalyzer(series=SERIES, config=liq, analysis_config=analysis)
    displacement = DisplacementAnalyzer(disp, price_unit=liq.price_unit)
    gaps = FVGAnalyzer(fvg)
    blocks = OrderBlockAnalyzer(ob)
    zones = PDAnalyzer(pd)
    engine = MSSAnalyzer(mss)
    actual = []
    for candle in provider.replay():
        source = liquidity.update(candle)
        impulse = displacement.update(source)
        gap = gaps.update(impulse)
        block = blocks.update(gap)
        actual.append(engine.update(zones.update(block)))
    assert tuple(actual) == expected
    assert [
        (e.detection_index, e.direction.value, e.evidence.level_price) for e in events(actual)
    ] == [(22, "bearish", 13), (27, "bullish", 15)]


def test_mocked_public_binance_normalization_reuses_existing_provider():
    candles = golden()
    rows = []
    for c in candles:
        opening = (c.timestamp - EPOCH) // timedelta(milliseconds=1)
        rows.append(
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
    assert analyze_mss(upstream(canonical, series=declared)) == analyze_mss(
        upstream(candles, series=declared)
    )
