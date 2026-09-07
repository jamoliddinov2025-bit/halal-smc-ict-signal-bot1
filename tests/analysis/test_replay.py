"""Batch/stream/chunk equivalence and integration with the existing data layer."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from smcsignal.analysis import (
    AnalysisConfig,
    AnalysisInputError,
    MarketStructureAnalyzer,
    analyze,
    detect_swings,
    load_analysis_config,
)
from smcsignal.data import (
    BinancePublicDataProvider,
    CsvDataProvider,
    MarketDataConfig,
    create_data_provider,
    load_data_config,
)
from smcsignal.data.models import EPOCH
from tests.analysis.helpers import mirrored


@pytest.mark.parametrize("length", [3, 5, 7, 9])
def test_streamed_updates_match_batch_results(golden_candles, length):
    config = AnalysisConfig(length)
    engine = MarketStructureAnalyzer(config)
    assert engine.latest is None
    streamed = tuple(engine.update(candle) for candle in golden_candles)
    assert streamed == analyze(golden_candles, config)
    assert engine.latest is streamed[-1]


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 14, 20])
def test_arbitrary_replay_chunk_boundaries_preserve_state(golden_candles, chunk_size):
    engine = MarketStructureAnalyzer(AnalysisConfig(3))
    outputs = []
    for start in range(0, len(golden_candles), chunk_size):
        outputs.extend(engine.update(c) for c in golden_candles[start : start + chunk_size])
    assert tuple(outputs) == analyze(golden_candles, AnalysisConfig(3))


def test_independent_analyzers_do_not_share_state(golden_candles):
    first, second = (
        MarketStructureAnalyzer(AnalysisConfig(3)),
        MarketStructureAnalyzer(AnalysisConfig(3)),
    )
    inverse = mirrored(golden_candles)
    one, two = [], []
    for candle, other in zip(golden_candles, inverse, strict=True):
        one.append(first.update(candle))
        two.append(second.update(other))
    assert tuple(one) == analyze(golden_candles, AnalysisConfig(3))
    assert tuple(two) == analyze(inverse, AnalysisConfig(3))


def test_batch_accepts_a_single_pass_generator_without_sorting(golden_candles):
    class OnePass:
        def __init__(self):
            self.calls = 0

        def __iter__(self):
            self.calls += 1
            assert self.calls == 1
            yield from golden_candles

        def __len__(self):
            pytest.fail("analyze must not require the final series length")

    source = OnePass()
    assert analyze(source, AnalysisConfig(3)) == analyze(golden_candles, AnalysisConfig(3))
    assert source.calls == 1


def test_csv_replay_matches_direct_canonical_candles(golden_candles):
    path = Path(__file__).resolve().parents[1] / "fixtures" / "market_structure.csv"
    provider = CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT", timeframe="15m", data_source="csv", history_limit=500, csv_path=path
        )
    )
    assert provider.fetch_ohlcv().candles == golden_candles
    assert analyze(provider.replay(), AnalysisConfig(3)) == analyze(
        golden_candles, AnalysisConfig(3)
    )


def test_binance_normalization_feeds_identical_analysis_offline(golden_candles):
    payload = []
    for candle in golden_candles:
        opening = (candle.timestamp - EPOCH) // timedelta(milliseconds=1)
        payload.append(
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
    config = MarketDataConfig(
        symbol="BTCUSDT", timeframe="15m", data_source="binance_public", history_limit=500
    )
    provider = BinancePublicDataProvider(
        config,
        transport=lambda url, timeout: payload,
        clock=lambda: golden_candles[-1].timestamp + timedelta(hours=1),
    )
    assert analyze(provider.fetch_ohlcv().candles, AnalysisConfig(3)) == analyze(
        golden_candles, AnalysisConfig(3)
    )


def test_documented_end_to_end_example_has_real_structure_outputs():
    path = Path(__file__).resolve().parents[2] / "config" / "analysis.example.toml"
    candles = create_data_provider(load_data_config(path)).fetch_ohlcv().candles
    results = analyze(candles, load_analysis_config(path))
    assert len(results) == 14
    assert [event.candle_index for snapshot in results for event in snapshot.events] == [
        6,
        8,
        12,
        13,
    ]


@pytest.mark.parametrize("bad", [None, {}, [], "candle"])
def test_noncanonical_input_is_rejected_without_mutating_existing_state(golden_candles, bad):
    engine = MarketStructureAnalyzer(AnalysisConfig(3))
    collected = [engine.update(c) for c in golden_candles[:5]]
    previous = engine.latest
    with pytest.raises(AnalysisInputError, match="OHLCV"):
        engine.update(bad)
    assert engine.latest is previous
    collected.extend(engine.update(c) for c in golden_candles[5:])
    assert tuple(collected) == analyze(golden_candles, AnalysisConfig(3))


@pytest.mark.parametrize("bad_index", [0, 2, 4])
def test_duplicate_and_out_of_order_updates_are_atomic(golden_candles, bad_index):
    engine = MarketStructureAnalyzer(AnalysisConfig(3))
    collected = [engine.update(c) for c in golden_candles[:5]]
    previous = engine.latest
    with pytest.raises(AnalysisInputError, match="chronological"):
        engine.update(golden_candles[bad_index])
    assert engine.latest is previous
    collected.extend(engine.update(c) for c in golden_candles[5:])
    assert tuple(collected) == analyze(golden_candles, AnalysisConfig(3))


def test_same_timestamp_price_revision_is_not_an_in_place_history_edit(golden_candles):
    engine = MarketStructureAnalyzer(AnalysisConfig(3))
    engine.update(golden_candles[0])
    with pytest.raises(AnalysisInputError, match="chronological"):
        engine.update(replace(golden_candles[0], close=golden_candles[0].high))
    assert engine.latest == analyze(golden_candles[:1], AnalysisConfig(3))[0]


def test_batch_does_not_silently_reorder_candles(golden_candles):
    with pytest.raises(AnalysisInputError, match="chronological"):
        analyze(tuple(reversed(golden_candles)), AnalysisConfig(3))


@pytest.mark.parametrize("source", [None, 42])
def test_noniterable_batch_input_is_actionable(source):
    with pytest.raises(AnalysisInputError, match="iterable"):
        analyze(source)
    with pytest.raises(AnalysisInputError, match="iterable"):
        detect_swings(source)


def test_empty_input_and_fresh_stream_have_no_outputs():
    assert analyze([]) == ()
    assert detect_swings([]) == ()
    assert MarketStructureAnalyzer().latest is None
