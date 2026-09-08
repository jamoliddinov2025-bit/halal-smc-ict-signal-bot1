from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from smcsignal.analysis import load_analysis_config
from smcsignal.analysis.breaker_blocks import (
    BreakerBlockAnalyzer,
    analyze_breaker_blocks,
    load_breaker_block_config,
)
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
from tests.breaker_blocks.helpers import SERIES, events, golden, upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "breaker-block.example.toml"


def test_default_csv_pipeline_and_incremental_processing_are_identical():
    data, analysis, liq, disp, fvg, ob, pd, mss, breaker = (
        load_data_config(PATH),
        load_analysis_config(PATH),
        load_liquidity_config(PATH),
        load_displacement_config(PATH),
        load_fvg_config(PATH),
        load_order_block_config(PATH),
        load_pd_config(PATH),
        load_mss_config(PATH),
        load_breaker_block_config(PATH),
    )
    provider = CsvDataProvider(data)
    candles = provider.fetch_ohlcv().candles
    assert candles == golden()
    a = analyze_liquidity(candles, series=SERIES, config=liq, analysis_config=analysis)
    b = analyze_displacement(a, disp, price_unit=liq.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, fvg), ob)
    d = analyze_mss(analyze_pd(c, pd), mss)
    expected = analyze_breaker_blocks(d, breaker)
    liquidity = LiquidityAnalyzer(series=SERIES, config=liq, analysis_config=analysis)
    displacement = DisplacementAnalyzer(disp, price_unit=liq.price_unit)
    gaps = FVGAnalyzer(fvg)
    blocks = OrderBlockAnalyzer(ob)
    zones = PDAnalyzer(pd)
    shifts = MSSAnalyzer(mss)
    engine = BreakerBlockAnalyzer(breaker)
    actual = []
    for candle in provider.replay():
        source = liquidity.update(candle)
        impulse = displacement.update(source)
        block = blocks.update(gaps.update(impulse))
        shift = shifts.update(zones.update(block))
        actual.append(engine.update(shift))
    assert tuple(actual) == expected
    assert [
        (e.original_ob_confirmation_index, e.confirmation_index, e.direction.value)
        for e in events(actual)
    ] == [(20, 22, "bearish"), (22, 27, "bullish")]


def test_mocked_public_binance_mapping_uses_existing_provider():
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
    assert analyze_breaker_blocks(upstream(canonical, series=declared)) == analyze_breaker_blocks(
        upstream(candles, series=declared)
    )
