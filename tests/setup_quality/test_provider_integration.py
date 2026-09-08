from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from smcsignal.analysis import load_analysis_config, load_liquidity_config
from smcsignal.analysis.displacement import load_displacement_config
from smcsignal.analysis.fvg import load_fvg_config
from smcsignal.analysis.halal_filter import analyze_halal, load_halal_filter_config
from smcsignal.analysis.mtf import analyze_mtf, load_mtf_config
from smcsignal.analysis.order_blocks import load_order_block_config
from smcsignal.analysis.ote import analyze_ote, load_ote_config
from smcsignal.analysis.premium_discount import load_pd_config
from smcsignal.analysis.setup_quality import (
    SetupQualityAnalyzer,
    analyze_setup_quality,
    load_setup_quality_config,
)
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.halal_filter.helpers import mtf_for, series
from tests.mtf.helpers import higher, series_for
from tests.ote.helpers import upstream

PATH = Path(__file__).resolve().parents[2] / "config" / "setup-quality.example.toml"
ROOT = Path(__file__).resolve().parents[2]


def _ote(candles, timeframe, *, symbol="BTCUSDT"):
    path = PATH
    declared = series(symbol, timeframe) if symbol != "BTCUSDT" else series_for(timeframe)
    return analyze_ote(
        upstream(
            candles,
            series=declared,
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
    halal = load_halal_filter_config(PATH)
    config = load_setup_quality_config(PATH)
    primary_candles = CsvDataProvider(data).fetch_ohlcv().candles
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
    filtered = analyze_halal(analyze_mtf(primary, frames, mtf), halal)
    expected = analyze_setup_quality(filtered, config)
    engine = SetupQualityAnalyzer(config)
    actual = tuple(engine.update(frame) for frame in filtered)
    assert actual == expected
    assert actual[0].total == 10
    assert actual[0].threshold_passed is False
    assert actual[4].total == 25
    assert actual[4].threshold_passed is False
    assert actual[0].upstream is filtered[0]


def test_mocked_public_binance_normalization_uses_existing_providers():
    candles = tuple(frame.upstream.upstream.observation.candle for frame in mtf_for())
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
    frames = analyze_setup_quality(analyze_halal(analyze_mtf(primary, higher())))
    assert frames[0].total == 10
    assert frames[4].total == 25
    assert frames[0].threshold_passed is False


def test_lowercase_series_symbol_does_not_rewrite_identity_or_score():
    primary = _ote(
        CsvDataProvider(load_data_config(PATH)).fetch_ohlcv().candles,
        "15m",
        symbol="btcusdt",
    )
    hourly = _ote(
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
        .candles,
        "1h",
        symbol="btcusdt",
    )
    four = _ote(
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
        .candles,
        "4h",
        symbol="btcusdt",
    )
    filtered = analyze_halal(
        analyze_mtf(primary, {"1h": hourly, "4h": four}, load_mtf_config(PATH))
    )
    frames = analyze_setup_quality(filtered, load_setup_quality_config(PATH))
    assert frames[0].upstream.decision.symbol == "BTCUSDT"
    assert frames[0].total == 10
    assert frames[0].provenance.series.symbol == "btcusdt"
    assert frames[4].total == 25
