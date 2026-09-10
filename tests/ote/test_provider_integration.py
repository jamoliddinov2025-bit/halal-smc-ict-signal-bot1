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
from smcsignal.analysis.ote import OTEAnalyzer, OTEClassification, analyze_ote, load_ote_config
from smcsignal.analysis.premium_discount import PDAnalyzer, analyze_pd, load_pd_config
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.ote.helpers import SERIES, golden, upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "ote.example.toml"


def test_documented_csv_default_pipeline_and_stream_match():
    data, analysis, liq, disp, fvg, ob, pd, ote = (
        load_data_config(PATH),
        load_analysis_config(PATH),
        load_liquidity_config(PATH),
        load_displacement_config(PATH),
        load_fvg_config(PATH),
        load_order_block_config(PATH),
        load_pd_config(PATH),
        load_ote_config(PATH),
    )
    provider = CsvDataProvider(data)
    candles = provider.fetch_ohlcv().candles
    assert candles == golden()
    a = analyze_liquidity(candles, series=SERIES, config=liq, analysis_config=analysis)
    b = analyze_displacement(a, disp, price_unit=liq.price_unit)
    expected = analyze_ote(analyze_pd(analyze_order_blocks(analyze_fvg(b, fvg), ob), pd), ote)
    liquidity = LiquidityAnalyzer(series=SERIES, config=liq, analysis_config=analysis)
    displacement = DisplacementAnalyzer(disp, price_unit=liq.price_unit)
    gaps = FVGAnalyzer(fvg)
    blocks = OrderBlockAnalyzer(ob)
    premium = PDAnalyzer(pd)
    engine = OTEAnalyzer(ote)
    actual = tuple(
        engine.update(
            premium.update(blocks.update(gaps.update(displacement.update(liquidity.update(c)))))
        )
        for c in provider.replay()
    )
    assert actual == expected
    assert [f.classification for f in actual] == [
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSIDE_OTE,
        OTEClassification.BELOW_OTE,
        OTEClassification.ABOVE_OTE,
        OTEClassification.INSIDE_OTE,
        OTEClassification.INSIDE_OTE,
    ]


def test_mocked_public_binance_normalization_uses_existing_providers():
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
    assert analyze_ote(upstream(canonical, series=declared)) == analyze_ote(
        upstream(candles, series=declared)
    )
