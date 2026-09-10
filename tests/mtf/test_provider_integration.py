from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from smcsignal.analysis import load_analysis_config, load_liquidity_config
from smcsignal.analysis.displacement import load_displacement_config
from smcsignal.analysis.fvg import load_fvg_config
from smcsignal.analysis.mtf import MTFAnalyzer, MTFDirection, analyze_mtf, load_mtf_config
from smcsignal.analysis.order_blocks import load_order_block_config
from smcsignal.analysis.ote import analyze_ote, load_ote_config
from smcsignal.analysis.premium_discount import load_pd_config
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.mtf.helpers import higher, primary_15m, series_for
from tests.ote.helpers import upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "mtf.example.toml"
ROOT = Path(__file__).resolve().parents[2]


def _ote(candles, timeframe):
    path = PATH
    return analyze_ote(
        upstream(
            candles,
            series=series_for(timeframe),
            analysis=load_analysis_config(path),
            liquidity=load_liquidity_config(path),
            displacement=load_displacement_config(path),
            fvg=load_fvg_config(path),
            order_blocks=load_order_block_config(path),
            pd=load_pd_config(path),
        ),
        load_ote_config(path),
    )


def test_documented_csv_default_pipeline_and_stream_match():
    data = load_data_config(PATH)
    mtf = load_mtf_config(PATH)
    primary_candles = CsvDataProvider(data).fetch_ohlcv().candles
    assert primary_candles == tuple(frame.upstream.observation.candle for frame in primary_15m())
    hourly = (
        CsvDataProvider(
            MarketDataConfig(
                symbol="BTCUSDT",
                timeframe="1h",
                data_source="csv",
                history_limit=500,
                csv_path=ROOT / "tests/fixtures/mtf-1h.csv",
            )
        )
        .fetch_ohlcv()
        .candles
    )
    four = (
        CsvDataProvider(
            MarketDataConfig(
                symbol="BTCUSDT",
                timeframe="4h",
                data_source="csv",
                history_limit=500,
                csv_path=ROOT / "tests/fixtures/mtf-4h.csv",
            )
        )
        .fetch_ohlcv()
        .candles
    )
    primary = _ote(primary_candles, "15m")
    frames = {"1h": _ote(hourly, "1h"), "4h": _ote(four, "4h")}
    expected = analyze_mtf(primary, frames, mtf)
    engine = MTFAnalyzer(mtf, higher=frames)
    actual = tuple(engine.update(frame) for frame in primary)
    assert actual == expected
    assert actual[4].direction is MTFDirection.BULLISH
    assert actual[4].relations[1].latest is None
    assert actual[16].relations[1].latest is not None
    assert actual[16].relations[1].latest.provenance.available_at.hour == 12


def test_mocked_public_binance_normalization_uses_existing_providers():
    candles = tuple(frame.upstream.observation.candle for frame in primary_15m())
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
    declared = replace(series_for("15m"), provider="binance_public", venue="mock_binance_spot")
    primary = analyze_ote(upstream(canonical, series=declared))
    assert analyze_mtf(primary, higher())[4].direction is MTFDirection.BULLISH
