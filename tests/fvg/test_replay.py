from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, AnalysisInputError
from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.fvg import analyze_fvg
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquidityConfig
from smcsignal.data import DataValidationError
from tests.analysis.helpers import bar
from tests.fvg.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    bullish,
    golden,
    run,
    upstream,
)


def test_batch_stream_and_original_upstream_object_identity():
    source = upstream(golden())
    engine = analyzer()
    actual = tuple(engine.update(frame) for frame in source)
    assert actual == analyze_fvg(source)
    assert all(f.upstream is original for f, original in zip(actual, source, strict=True))
    for frame in actual:
        assert all(any(item is original for original in source) for item in frame.window)
    assert engine.latest is actual[-1]
    assert engine.processed_count == len(source)
    assert engine.price_unit == "USDT" and engine.series == SERIES


@pytest.mark.parametrize("cut", range(21))
def test_all_chunk_boundaries_match_uninterrupted_processing(cut):
    source = upstream(golden())
    engine = analyzer()
    result = []
    for chunk in (source[:cut], (), source[cut:]):
        result.extend(engine.update(frame) for frame in chunk)
    assert tuple(result) == analyze_fvg(source)


def test_existing_pipeline_runs_once_per_candle_and_matches_precomputed_batch():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = analyzer()
    actual = tuple(fvg.update(displacement.update(liquidity.update(c))) for c in golden())
    assert actual == run(golden())
    assert (
        liquidity.processed_count
        == displacement.processed_count
        == fvg.processed_count
        == len(golden())
    )


def test_single_pass_and_empty_iterators():
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from upstream(bullish())

    assert analyze_fvg(Once()) == run(bullish())
    assert analyze_fvg(()) == ()
    engine = analyzer()
    assert engine.latest is None and engine.configuration_artifact is None
    assert engine.series is None and engine.price_unit is None and engine.processed_count == 0


def test_independent_interleaved_instances_have_identical_outputs():
    left, right = analyzer(), analyzer()
    for frame in upstream(golden()):
        assert left.update(frame) == right.update(frame)


@pytest.mark.parametrize("bad", [None, {}, (), "data", 0, bar(0, 100)])
def test_bad_or_raw_inputs_are_atomic(bad):
    source = upstream(bullish())
    engine = analyzer()
    first = engine.update(source[0])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(bad)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (first, engine.update(source[1])) == analyze_fvg(source[:2])


@pytest.mark.parametrize("indices", [(1,), (0, 0), (0, 2)])
def test_missing_shifted_duplicate_or_reordered_upstream_indices_are_rejected(indices):
    source = upstream(bullish())
    engine = analyzer()
    for index in indices[:-1]:
        engine.update(source[index])
    state = (engine.latest, engine.processed_count)
    with pytest.raises(AnalysisInputError):
        engine.update(source[indices[-1]])
    assert (engine.latest, engine.processed_count) == state


def test_conflicting_upstream_history_is_not_silently_combined():
    candles = bullish()
    source = upstream(candles)
    modified = upstream((replace(candles[0], volume=Decimal(9)), *candles[1:]))
    engine = analyzer()
    engine.update(source[0])
    with pytest.raises(AnalysisInputError, match="discontinuous"):
        engine.update(modified[1])
    assert engine.processed_count == 1


@pytest.mark.parametrize("override", ["series", "analysis", "displacement", "unit"])
def test_upstream_series_units_or_configuration_cannot_switch_midstream(override):
    source = upstream(bullish())
    kwargs = {
        "series": {"series": replace(SERIES, dataset_id="another-origin")},
        "analysis": {"analysis": AnalysisConfig(5)},
        "displacement": {"displacement": DisplacementConfig(atr_period=2)},
        "unit": {"liquidity": LiquidityConfig("BTC")},
    }[override]
    changed = upstream(bullish(), **kwargs)
    engine = analyzer()
    engine.update(source[0])
    with pytest.raises(AnalysisInputError):
        engine.update(changed[1])


def test_visible_liquidity_configuration_is_bound_without_rerunning_detector():
    from tests.liquidity.helpers import golden as pool_candles

    original = upstream(pool_candles())
    changed = upstream(pool_candles(), liquidity=LiquidityConfig("USDT", Decimal(1)))
    engine = analyzer()
    for frame in original[:3]:
        engine.update(frame)
    with pytest.raises(AnalysisInputError, match="liquidity configuration"):
        engine.update(changed[3])


def test_time_gaps_remain_observed_bars_and_do_not_imply_a_filled_zone():
    candles = bullish()
    gapped = tuple(
        replace(c, timestamp=c.timestamp + timedelta(days=3 if index > 0 else 0))
        for index, c in enumerate(candles)
    )
    result = run(gapped)
    assert result[2].events[0].gap_size == 1
    assert result[2].events[0].c1.reference.closed_at == candles[0].timestamp + timedelta(
        minutes=15
    )


def test_invalid_ohlc_remains_the_existing_data_layers_responsibility():
    for changes in (
        {"high": None},
        {"close": Decimal("NaN")},
        {"low": Decimal(1000)},
        {"volume": Decimal(-1)},
    ):
        with pytest.raises(DataValidationError):
            replace(bullish()[0], **changes)


def test_numeric_resource_error_is_atomic_without_rounding_into_a_gap():
    # Early Phase 5 warmup needs no ATR yet; each candle itself has zero range.
    tiny = bar(0, 1, opening=1, high=1, low=1)
    huge = bar(
        1,
        Decimal("1e5000"),
        opening=Decimal("1e5000"),
        high=Decimal("1e5000"),
        low=Decimal("1e5000"),
    )
    bad_last = replace(huge, timestamp=bar(2, 100).timestamp)
    good_last = replace(tiny, timestamp=bad_last.timestamp)
    bad = upstream((tiny, huge, bad_last), displacement=DisplacementConfig())
    good = upstream((tiny, huge, good_last), displacement=DisplacementConfig())
    engine = analyzer()
    before = tuple(engine.update(frame) for frame in bad[:2])
    with pytest.raises(AnalysisInputError, match="4096"):
        engine.update(bad[2])
    assert engine.latest is before[-1] and engine.processed_count == 2
    assert (*before, engine.update(good[2])) == analyze_fvg(good)


def test_noniterable_batch_input_is_an_explicit_error():
    with pytest.raises(AnalysisInputError):
        analyze_fvg(None)
