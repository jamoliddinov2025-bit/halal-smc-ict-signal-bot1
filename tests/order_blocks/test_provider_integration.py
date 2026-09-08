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
from smcsignal.analysis.order_blocks import (
    OrderBlockAnalyzer,
    analyze_order_blocks,
    load_order_block_config,
)
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.order_blocks.helpers import SERIES, events, golden, upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "order-block.example.toml"


def test_default_example_csv_batch_and_stream_use_each_existing_engine_once():
    data, analysis, liq, disp, fvg, ob = (
        load_data_config(PATH),
        load_analysis_config(PATH),
        load_liquidity_config(PATH),
        load_displacement_config(PATH),
        load_fvg_config(PATH),
        load_order_block_config(PATH),
    )
    provider = CsvDataProvider(data)
    candles = provider.fetch_ohlcv().candles
    assert candles == golden()
    base = analyze_liquidity(candles, series=SERIES, config=liq, analysis_config=analysis)
    middle = analyze_displacement(base, disp, price_unit=liq.price_unit)
    expected = analyze_order_blocks(analyze_fvg(middle, fvg), ob)
    liquidity = LiquidityAnalyzer(series=SERIES, config=liq, analysis_config=analysis)
    displacement = DisplacementAnalyzer(disp, price_unit=liq.price_unit)
    gaps = FVGAnalyzer(fvg)
    blocks = OrderBlockAnalyzer(ob)
    actual = tuple(
        blocks.update(gaps.update(displacement.update(liquidity.update(c))))
        for c in provider.replay()
    )
    assert actual == expected
    assert [
        (e.candidate_index, e.confirmation_index, e.direction.value) for e in events(actual)
    ] == [(18, 20, "bullish"), (21, 22, "bearish")]
    confirmed = analyze_order_blocks(analyze_fvg(middle, fvg), replace(ob, require_fvg=True))
    assert [(e.candidate_index, e.confirmation_index) for e in events(confirmed)] == [
        (18, 21),
        (21, 23),
    ]


def test_mocked_public_binance_data_flows_through_existing_layers():
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
    assert analyze_order_blocks(upstream(canonical, series=declared)) == analyze_order_blocks(
        upstream(candles, series=declared)
    )
