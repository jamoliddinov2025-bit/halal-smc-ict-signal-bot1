from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisInputError, analyze
from smcsignal.analysis.liquidity import PoolStatus
from smcsignal.data import CsvDataProvider, MarketDataConfig
from tests.analysis.helpers import bar
from tests.liquidity.helpers import ANALYSIS, analyzer, golden, run


def test_batch_stream_and_existing_phase_three_results_match():
    candles = golden()
    engine = analyzer()
    streamed = tuple(engine.update(c) for c in candles)
    assert streamed == run(candles)
    assert tuple(frame.context.snapshot for frame in streamed) == analyze(candles, ANALYSIS)
    assert engine.latest == streamed[-1]
    assert engine.processed_count == len(candles)
    assert engine.input_prefix_hash == streamed[-1].context.provenance.input_prefix_hash


@pytest.mark.parametrize("cut", range(13))
def test_arbitrary_chunk_boundaries_do_not_reset_state(cut):
    candles = golden()
    engine = analyzer()
    outputs = []
    for chunk in (candles[:cut], (), candles[cut:]):
        outputs.extend(engine.update(c) for c in chunk)
    assert tuple(outputs) == run(candles)


def test_single_pass_iterables_are_not_peeked_or_rewound():
    class Once:
        def __init__(self):
            self.started = False

        def __iter__(self):
            assert not self.started
            self.started = True
            yield from golden()

    assert run(Once()) == run(golden())


def test_empty_stream_emits_nothing():
    engine = analyzer()
    assert run(()) == ()
    assert engine.latest is None and engine.active_pools == () and engine.processed_count == 0


def test_replay_active_state_can_be_reconstructed_from_public_deltas():
    engine = analyzer()
    state = {}
    for candle in golden():
        frame = engine.update(candle)
        for pool in frame.pool_updates:
            if pool.status == PoolStatus.ACTIVE:
                state[pool.pool_id] = pool
            else:
                del state[pool.pool_id]
        assert tuple(state.values()) == engine.active_pools


@pytest.mark.parametrize("bad", [None, {}, (), "data", 1])
def test_noncanonical_update_is_atomic(bad):
    engine = analyzer()
    first = engine.update(golden()[0])
    state = (engine.latest, engine.active_pools, engine.input_prefix_hash, engine.processed_count)
    with pytest.raises(AnalysisInputError):
        engine.update(bad)
    assert (
        engine.latest,
        engine.active_pools,
        engine.input_prefix_hash,
        engine.processed_count,
    ) == state
    assert (first, engine.update(golden()[1])) == run(golden()[:2])


@pytest.mark.parametrize(
    "bad",
    [
        bar(0, 10),
        bar(1, 10),
        replace(bar(2, 10), timestamp=bar(1, 10).timestamp + timedelta(minutes=1)),
    ],
)
def test_duplicates_backwards_and_overlapping_intervals_are_atomic(bad):
    engine = analyzer()
    before = tuple(engine.update(c) for c in golden()[:2])
    last_hash = engine.input_prefix_hash
    with pytest.raises(AnalysisInputError):
        engine.update(bad)
    assert engine.latest == before[-1] and engine.input_prefix_hash == last_hash
    assert (*before, engine.update(golden()[2])) == run(golden()[:3])


@pytest.mark.parametrize("bad", [datetime(2024, 1, 1), "later", False, bar(1, 10).timestamp])
def test_bad_availability_is_atomic(bad):
    engine = analyzer()
    first = engine.update(golden()[0])
    state = (engine.latest, engine.input_prefix_hash, engine.processed_count)
    with pytest.raises(AnalysisInputError):
        engine.update(golden()[1], available_at=bad)
    assert (engine.latest, engine.input_prefix_hash, engine.processed_count) == state
    assert (first, engine.update(golden()[1])) == run(golden()[:2])


def test_arrival_clock_cannot_rewind():
    engine = analyzer()
    engine.update(golden()[0], available_at=golden()[2].timestamp + timedelta(hours=1))
    before = engine.input_prefix_hash
    with pytest.raises(AnalysisInputError, match="rewind"):
        engine.update(golden()[1])
    assert engine.processed_count == 1 and engine.input_prefix_hash == before


def test_gaps_do_not_use_the_next_row_as_previous_candle_close():
    candles = golden()
    gapped = tuple(
        replace(c, timestamp=c.timestamp + timedelta(days=1 if i >= 3 else 0))
        for i, c in enumerate(candles)
    )
    frames = run(gapped)
    assert frames[2].context.observation.reference.closed_at == candles[2].timestamp + timedelta(
        minutes=15
    )
    assert frames[3].context.observation.reference.candle_index == 3
    assert [e.breach.reference.candle_index for f in frames for e in f.sweeps] == [9, 10]


def test_two_interleaved_instances_have_no_shared_state():
    left, right = analyzer(), analyzer()
    expected = run(golden())
    for index, candle in enumerate(golden()):
        assert left.update(candle) == expected[index]
        assert right.update(candle) == expected[index]


def test_explicit_delayed_arrival_replay_is_deterministic():
    def replay():
        engine = analyzer()
        return tuple(
            engine.update(c, available_at=c.timestamp + timedelta(minutes=16)) for c in golden()
        )

    assert replay() == replay()


def test_csv_fetch_and_replay_feed_the_same_engine():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "liquidity.csv"
    provider = CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="csv",
            history_limit=100,
            csv_path=fixture,
        )
    )
    assert provider.fetch_ohlcv().candles == golden()
    assert run(provider.replay()) == run(golden())


def test_noniterable_batch_input():
    with pytest.raises(AnalysisInputError):
        run(None)


def test_mock_binance_normalization_integrates_without_live_access():
    from smcsignal.data import BinancePublicDataProvider
    from smcsignal.data.models import EPOCH

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
    assert provider.fetch_ohlcv().candles == candles
    declared = replace(analyzer().series, venue="mock_binance_spot", provider="binance_public")
    assert run(provider.fetch_ohlcv().candles, series=declared) == run(candles, series=declared)
