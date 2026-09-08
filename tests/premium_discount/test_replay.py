from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, AnalysisInputError
from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.fvg import FVGAnalyzer, FVGConfig
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquidityConfig
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer, OrderBlockConfig
from smcsignal.analysis.premium_discount import PDConfig, analyze_pd
from smcsignal.data import DataValidationError
from tests.analysis.helpers import bar
from tests.order_blocks.helpers import golden as all_candles
from tests.premium_discount.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    FVG,
    LIQUIDITY,
    OB,
    SERIES,
    analyzer,
    golden,
    run,
    upstream,
)


def test_batch_and_stream_reuse_exact_upstream_objects():
    raw = upstream(all_candles(), displacement=DisplacementConfig())
    engine = analyzer()
    actual = tuple(engine.update(f) for f in raw)
    assert actual == analyze_pd(raw)
    assert all(f.upstream is original for f, original in zip(actual, raw, strict=True))
    assert engine.latest is actual[-1] and engine.processed_count == len(raw)
    assert engine.series == SERIES and engine.price_unit == "USDT"


@pytest.mark.parametrize("cut", range(8))
@pytest.mark.parametrize("fraction", ["0", "0.1"])
def test_every_chunk_boundary_is_identical(cut, fraction):
    frames = upstream(golden())
    engine = analyzer(fraction)
    outputs = []
    for chunk in (frames[:cut], (), frames[cut:]):
        outputs.extend(engine.update(f) for f in chunk)
    assert tuple(outputs) == analyze_pd(frames, PDConfig(Decimal(fraction)))


def test_full_pipeline_stream_is_not_recomputed_by_pd():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer(FVG)
    blocks = OrderBlockAnalyzer(OB)
    pd = analyzer()
    result = tuple(
        pd.update(blocks.update(gaps.update(displacement.update(liquidity.update(c)))))
        for c in golden()
    )
    assert result == run(golden())
    assert (
        liquidity.processed_count
        == displacement.processed_count
        == gaps.processed_count
        == blocks.processed_count
        == pd.processed_count
        == 7
    )


def test_empty_and_single_pass_inputs():
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from upstream(golden())

    assert analyze_pd(Once()) == run(golden())
    assert analyze_pd(()) == ()
    engine = analyzer()
    assert engine.series is None and engine.price_unit is None and engine.latest is None
    assert engine.configuration_artifact is None and engine.range_configuration_artifact is None


def test_independent_interleaved_engines_do_not_share_state():
    first, second = analyzer(), analyzer()
    for f in upstream(golden()):
        assert first.update(f) == second.update(f)


@pytest.mark.parametrize("value", [None, {}, "data", True, bar(0, 100)])
def test_invalid_raw_inputs_are_atomic(value):
    frames = upstream(golden())
    engine = analyzer()
    before = tuple(engine.update(f) for f in frames[:4])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(value)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (*before, engine.update(frames[4])) == analyze_pd(frames[:5])


@pytest.mark.parametrize("indices", [(1,), (0, 0), (0, 2)])
def test_missing_duplicate_or_shifted_observed_indices_are_errors(indices):
    source = upstream(golden())
    engine = analyzer()
    for i in indices[:-1]:
        engine.update(source[i])
    state = (engine.latest, engine.processed_count)
    with pytest.raises(AnalysisInputError):
        engine.update(source[indices[-1]])
    assert (engine.latest, engine.processed_count) == state


@pytest.mark.parametrize(
    "change", ["series", "analysis", "displacement", "fvg", "order_blocks", "unit", "history"]
)
def test_upstream_context_and_configuration_cannot_be_switched(change):
    candles = golden()
    raw = upstream(candles)
    kwargs = {
        "series": {"series": replace(SERIES, timeframe="5m")},
        "analysis": {"analysis": AnalysisConfig(5)},
        "displacement": {"displacement": DisplacementConfig(atr_period=2)},
        "fvg": {"fvg": FVGConfig(min_gap_size=Decimal("0.1"))},
        "order_blocks": {"order_blocks": OrderBlockConfig(require_fvg=True)},
        "unit": {"liquidity": LiquidityConfig("BTC")},
        "history": {},
    }[change]
    alternate = (
        (replace(candles[0], volume=Decimal(9)), *candles[1:]) if change == "history" else candles
    )
    changed = upstream(alternate, **kwargs)
    engine = analyzer()
    first = engine.update(raw[0])
    with pytest.raises(AnalysisInputError):
        engine.update(changed[1])
    assert engine.latest is first


def test_visible_liquidity_config_is_bound():
    source = upstream(golden())
    changed = upstream(golden(), liquidity=LiquidityConfig("USDT", Decimal(1)))
    engine = analyzer()
    for f in source[:3]:
        engine.update(f)
    with pytest.raises(AnalysisInputError):
        engine.update(changed[3])


def test_elapsed_gaps_do_not_pad_or_guess_ranges():
    candles = tuple(
        replace(c, timestamp=c.timestamp + timedelta(days=2 if i >= 3 else 0))
        for i, c in enumerate(golden())
    )
    results = run(candles)
    assert [f.classification for f in results] == [f.classification for f in run(golden())]
    assert results[3].latest_low.confirmation.reference.closed_at == candles[
        3
    ].timestamp + timedelta(minutes=15)


def test_late_confirmation_is_known_only_at_supplied_availability():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = analyzer()
    result = []
    for i, c in enumerate(golden()):
        known = c.timestamp + timedelta(minutes=16, microseconds=1) if i == 3 else None
        result.append(
            pd.update(
                blocks.update(
                    gaps.update(displacement.update(liquidity.update(c, available_at=known)))
                )
            )
        )
    assert result[3].dealing_range.available_at == result[3].observation.available_at
    assert result[3].dealing_range.available_at > result[3].observation.reference.closed_at
    assert result[2].dealing_range is None


def test_bad_source_values_still_fail_existing_data_validation():
    for values in ({"close": Decimal("NaN")}, {"low": None}, {"high": Decimal(1)}):
        with pytest.raises(DataValidationError):
            replace(golden()[0], **values)


def test_noniterable_input_fails():
    with pytest.raises(AnalysisInputError):
        analyze_pd(None)


def test_equilibrium_numeric_resource_failure_does_not_commit_new_pair_or_state():
    frames = upstream(golden())
    engine = analyzer("1e-5000")
    before = tuple(engine.update(f) for f in frames[:3])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError, match="4096"):
        engine.update(frames[3])
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert engine.latest is before[-1] and engine.latest.latest_low is None
