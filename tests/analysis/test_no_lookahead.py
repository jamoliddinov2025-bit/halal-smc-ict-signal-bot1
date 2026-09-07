"""Causality proofs by prefix replay, future shocks, and an independent oracle."""

from dataclasses import asdict
from random import Random

import pytest

from smcsignal.analysis import (
    AnalysisConfig,
    MarketStructureAnalyzer,
    StructureEventKind,
    SwingKind,
    analyze,
    detect_swings,
)
from tests.analysis.helpers import bar, series


def generated(seed, length=55):
    random = Random(seed)
    candles = []
    for index in range(length):
        center = random.randrange(60, 141)
        candles.append(
            bar(
                index,
                center,
                high=center + random.randrange(1, 10),
                low=center - random.randrange(1, 10),
                volume=random.randrange(0, 100),
            )
        )
    return tuple(candles)


@pytest.mark.parametrize("length", [3, 5, 7, 9])
@pytest.mark.parametrize("seed", [0, 17, 2026])
def test_every_prefix_matches_corresponding_full_series_snapshots(length, seed):
    candles = generated(seed)
    config = AnalysisConfig(length)
    full = analyze(candles, config)
    assert any(snapshot.confirmed_swings for snapshot in full)
    for cut in range(len(candles) + 1):
        assert analyze(candles[:cut], config) == full[:cut], (length, seed, cut)


@pytest.mark.parametrize("length", [3, 5, 7, 9])
@pytest.mark.parametrize("seed", [1, 19, 2026])
def test_replacing_all_future_prices_never_changes_historical_snapshots(length, seed):
    candles = generated(seed)
    config = AnalysisConfig(length)
    expected = analyze(candles, config)
    for cut in range(len(candles) + 1):
        future = tuple(
            bar(index, 2 if index % 2 else 1000000) for index in range(cut, len(candles))
        )
        changed = analyze(candles[:cut] + future, config)
        assert changed[:cut] == expected[:cut], (length, seed, cut)


@pytest.mark.parametrize("length", [3, 5, 7, 9])
def test_appending_future_candles_does_not_repaint_the_previous_tail(length):
    candles = generated(7)
    before = analyze(candles, AnalysisConfig(length))
    future = tuple(
        bar(index, 3 if index % 2 else 1000000) for index in range(len(candles), len(candles) + 20)
    )
    assert analyze(candles + future, AnalysisConfig(length))[: len(candles)] == before


@pytest.mark.parametrize("length", [3, 5, 7])
def test_flat_swing_results_are_filtered_by_confirmation_not_pivot_time(length):
    candles = generated(17)
    config = AnalysisConfig(length)
    full = detect_swings(candles, config)
    for cut in range(len(candles) + 1):
        assert detect_swings(candles[:cut], config) == tuple(
            s for s in full if s.confirmed_index < cut
        )


def test_pending_pivot_can_be_confirmed_or_invalidated_without_rewriting_past():
    prefix = series([10, 12, 16])
    confirmed_future = prefix + (bar(3, 13), bar(4, 11))
    invalidated_future = prefix + (bar(3, 17), bar(4, 18))
    config = AnalysisConfig(5)
    first, second = analyze(confirmed_future, config), analyze(invalidated_future, config)
    assert first[:3] == second[:3] == analyze(prefix, config)
    assert all(snapshot.confirmed_swings == () for snapshot in first[:4])
    assert any(s.pivot_index == 2 and s.confirmed_index == 4 for s in first[4].confirmed_swings)
    assert second[4].confirmed_swings == ()


@pytest.mark.parametrize("length", [3, 5, 9])
def test_published_objects_do_not_mutate_during_future_stream_updates(length):
    candles = generated(42)
    engine = MarketStructureAnalyzer(AnalysisConfig(length))
    retained = tuple(engine.update(c) for c in candles[:30])
    copied = [asdict(snapshot) for snapshot in retained]
    independent_prefix = analyze(candles[:30], AnalysisConfig(length))
    for candle in candles[30:]:
        engine.update(candle)
    assert retained == independent_prefix
    assert [asdict(snapshot) for snapshot in retained] == copied


@pytest.mark.parametrize("length", [3, 5, 7, 9])
def test_confirmations_match_independent_closed_prefix_extrema_oracle(length):
    candles = generated(73)
    radius = (length - 1) // 2
    expected = []
    for t in range(length - 1, len(candles)):
        prefix = candles[: t + 1]
        window = prefix[-length:]
        candidate = window[radius]
        if sum(c.high >= candidate.high for c in window) == 1:
            expected.append((SwingKind.HIGH, t - radius, t, candidate.high))
        if sum(c.low <= candidate.low for c in window) == 1:
            expected.append((SwingKind.LOW, t - radius, t, candidate.low))
    actual = detect_swings(candles, AnalysisConfig(length))
    assert [(s.kind, s.pivot_index, s.confirmed_index, s.price) for s in actual] == expected


def test_causal_metadata_and_nonvacuous_bos_choch_prefix_proof(golden_candles):
    config = AnalysisConfig(3)
    result = analyze(golden_candles, config)
    all_events = [event for snapshot in result for event in snapshot.events]
    assert {event.kind for event in all_events} == {
        StructureEventKind.BOS,
        StructureEventKind.CHOCH,
    }
    for t, snapshot in enumerate(result):
        assert analyze(golden_candles[: t + 1], config)[-1] == snapshot
        assert snapshot.timestamp == golden_candles[t].timestamp
        for swing in snapshot.confirmed_swings:
            assert swing.confirmed_index == t == swing.pivot_index + config.confirmation_delay
            assert swing.confirmed_timestamp == golden_candles[t].timestamp
        for event in snapshot.events:
            assert event.level.confirmed_index < event.candle_index == t
            assert event.level.confirmed_timestamp < event.timestamp
            assert event.trend_before == result[t - 1].trend.direction
            assert event.previous_close == golden_candles[t - 1].close
            assert event.close == golden_candles[t].close
