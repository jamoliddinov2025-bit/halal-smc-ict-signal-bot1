from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, AnalysisInputError
from smcsignal.analysis.displacement import analyze_displacement
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquidityConfig
from smcsignal.data import DataValidationError
from tests.analysis.helpers import bar
from tests.displacement.helpers import (
    ANALYSIS,
    LIQUIDITY,
    SERIES,
    analyzer,
    bull,
    golden,
    settings,
    upstream,
    warmup,
)


def test_existing_frames_are_consumed_without_duplicate_upstream_analysis():
    frames = upstream(golden())
    engine = analyzer(atr_period=14)
    streamed = tuple(engine.update(frame) for frame in frames)
    assert streamed == analyze_displacement(frames, settings(atr_period=14), price_unit="USDT")
    assert all(
        result.liquidity is original for result, original in zip(streamed, frames, strict=True)
    )
    assert engine.latest is streamed[-1] and engine.processed_count == len(frames)
    assert engine.series == SERIES


@pytest.mark.parametrize("cut", range(21))
def test_every_chunk_boundary_matches_uninterrupted_replay(cut):
    frames = upstream(golden())
    engine = analyzer(atr_period=14)
    actual = []
    for chunk in (frames[:cut], (), frames[cut:]):
        actual.extend(engine.update(frame) for frame in chunk)
    assert tuple(actual) == analyze_displacement(frames, settings(atr_period=14), price_unit="USDT")


def test_two_stage_stream_equals_batched_pipeline():
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    detector = analyzer(atr_period=14)
    actual = tuple(detector.update(source.update(c)) for c in golden())
    assert actual == analyze_displacement(
        upstream(golden()), settings(atr_period=14), price_unit="USDT"
    )


def test_single_pass_iterator_and_empty_replay():
    class Once:
        started = False

        def __iter__(self):
            assert not self.started
            self.started = True
            yield from upstream(golden())

    assert analyze_displacement(Once(), settings(), price_unit="USDT") == analyze_displacement(
        upstream(golden()), settings(), price_unit="USDT"
    )
    assert analyze_displacement((), price_unit="USDT") == ()
    engine = analyzer()
    assert engine.latest is None and engine.processed_count == 0 and engine.series is None


def test_independent_interleaved_detectors_do_not_share_state():
    left, right = analyzer(), analyzer()
    for frame in upstream(golden()):
        assert left.update(frame) == right.update(frame)


@pytest.mark.parametrize("invalid", [None, {}, "data", 1, bar(0, 100)])
def test_raw_or_invalid_input_is_rejected_before_state_mutation(invalid):
    frames = upstream((*warmup(), bull()))
    engine = analyzer()
    initial = engine.update(frames[0])
    with pytest.raises(AnalysisInputError):
        engine.update(invalid)
    assert engine.latest is initial and engine.processed_count == 1
    assert (initial, engine.update(frames[1])) == analyze_displacement(
        frames[:2], settings(), price_unit="USDT"
    )


@pytest.mark.parametrize("indices", [(1,), (0, 0), (0, 2)])
def test_shifted_duplicate_or_missing_frames_are_not_silently_repaired(indices):
    frames = upstream(golden())
    engine = analyzer()
    for index in indices[:-1]:
        engine.update(frames[index])
    state = (engine.latest, engine.processed_count)
    with pytest.raises(AnalysisInputError):
        engine.update(frames[indices[-1]])
    assert (engine.latest, engine.processed_count) == state


def test_changed_series_cannot_be_mixed():
    original = upstream(golden())
    changed = upstream(golden(), series=replace(SERIES, timeframe="5m"))
    engine = analyzer()
    engine.update(original[0])
    with pytest.raises(AnalysisInputError, match="discontinuous"):
        engine.update(changed[1])


def test_changed_history_context_link_cannot_be_mixed():
    candles = golden()
    original = upstream(candles)
    changed = upstream((replace(candles[0], volume=Decimal(9)), *candles[1:]))
    engine = analyzer()
    engine.update(original[0])
    with pytest.raises(AnalysisInputError, match="discontinuous"):
        engine.update(changed[1])


def test_changed_structure_configuration_cannot_be_mixed():
    original = upstream(golden())
    changed = upstream(golden(), analysis=AnalysisConfig(5))
    engine = analyzer()
    engine.update(original[0])
    with pytest.raises(AnalysisInputError):
        engine.update(changed[1])


def test_changed_liquidity_configuration_or_price_unit_cannot_be_mixed():
    from tests.liquidity.helpers import golden as pool_candles

    original = upstream(pool_candles())
    changed = upstream(pool_candles(), liquidity=LiquidityConfig("USDT", Decimal(1)))
    engine = analyzer()
    for frame in original[:3]:
        engine.update(frame)
    before = engine.latest
    with pytest.raises(AnalysisInputError, match="liquidity configuration"):
        engine.update(changed[3])
    assert engine.latest is before
    wrong_unit = analyzer()
    for index, frame in enumerate(upstream(pool_candles(), liquidity=LiquidityConfig("BTC"))):
        if index < 2:
            wrong_unit.update(frame)
        else:
            with pytest.raises(AnalysisInputError, match="units"):
                wrong_unit.update(frame)
            break


def test_missing_or_invalid_raw_data_remains_the_existing_data_layers_responsibility():
    with pytest.raises(DataValidationError):
        replace(bull(), close=Decimal("NaN"))
    with pytest.raises(DataValidationError):
        replace(bull(), low=Decimal(103))
    with pytest.raises(DataValidationError):
        replace(bull(), volume=None)


def test_numeric_resource_failure_is_atomic_and_does_not_silently_round():
    valid = upstream((*warmup(), bull()))
    huge = upstream((*warmup(), bar(4, 102, opening=100, high=Decimal("1e5000"), low=99)))
    engine = analyzer()
    before = tuple(engine.update(frame) for frame in valid[:4])
    with pytest.raises(AnalysisInputError, match="4096"):
        engine.update(huge[4])
    assert engine.latest == before[-1] and engine.processed_count == 4
    assert (*before, engine.update(valid[4])) == analyze_displacement(
        valid, settings(), price_unit="USDT"
    )


def test_gaps_keep_observed_index_periods_and_use_previous_observed_close():
    candles = (*warmup(), bull())
    gapped = tuple(
        replace(c, timestamp=c.timestamp + timedelta(days=2 if i >= 2 else 0))
        for i, c in enumerate(candles)
    )
    actual = analyze_displacement(upstream(gapped), settings(), price_unit="USDT")
    assert actual[4].atr_reference.value == 2
    assert actual[4].events[0].detection_index == 4
    assert actual[1].liquidity.context.observation.reference.closed_at == candles[
        1
    ].timestamp + timedelta(minutes=15)


def test_delayed_arrival_replay_is_stable_and_preserves_true_availability():
    def replay():
        source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
        engine = analyzer()
        return tuple(
            engine.update(
                source.update(c, available_at=c.timestamp + timedelta(minutes=16, microseconds=1))
            )
            for c in (*warmup(), bull())
        )

    first = replay()
    assert first == replay()
    assert first[-1].events[0].available_at > first[-1].events[0].observation.reference.closed_at
    assert first[-1].atr_reference.provenance.available_at <= first[-1].events[0].available_at


def test_noniterable_batch_input_fails_explicitly():
    with pytest.raises(AnalysisInputError):
        analyze_displacement(None, price_unit="USDT")
