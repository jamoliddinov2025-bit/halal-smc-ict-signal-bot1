from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, AnalysisInputError
from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.fvg import FVGAnalyzer, FVGConfig
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquidityConfig
from smcsignal.analysis.mss import analyze_mss
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer, OrderBlockConfig
from smcsignal.analysis.premium_discount import PDAnalyzer, PDConfig
from smcsignal.data import DataValidationError
from tests.analysis.helpers import bar
from tests.mss.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    base,
    events,
    golden,
    run,
    upstream,
)


def test_batch_stream_original_frame_identity_and_read_only_state():
    raw = upstream(base())
    engine = analyzer()
    frames = tuple(engine.update(f) for f in raw)
    assert frames == analyze_mss(raw)
    assert all(f.upstream is original for f, original in zip(frames, raw, strict=True))
    assert engine.latest is frames[-1] and engine.processed_count == len(raw)
    assert engine.series == SERIES and engine.price_unit == "USDT"
    assert engine.configuration_artifact is not None


@pytest.mark.parametrize("cut", range(15))
def test_every_chunk_boundary_preserves_events_and_evidence(cut):
    raw = upstream(base())
    engine = analyzer()
    frames = []
    for chunk in (raw[:cut], (), raw[cut:]):
        frames.extend(engine.update(f) for f in chunk)
    assert tuple(frames) == analyze_mss(raw)


def test_existing_pipeline_runs_once_per_candle():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = analyzer()
    outputs = []
    for candle in base():
        observation = liquidity.update(candle)
        impulse = displacement.update(observation)
        gap = gaps.update(impulse)
        block = blocks.update(gap)
        outputs.append(mss.update(pd.update(block)))
    frames = tuple(outputs)
    assert frames == run(base())
    assert (
        liquidity.processed_count
        == displacement.processed_count
        == gaps.processed_count
        == blocks.processed_count
        == pd.processed_count
        == mss.processed_count
        == len(base())
    )


def test_empty_and_one_shot_iterators():
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from upstream(base())

    assert analyze_mss(Once()) == run(base())
    assert analyze_mss(()) == ()
    engine = analyzer()
    assert engine.latest is None and engine.series is None and engine.price_unit is None
    assert engine.configuration_artifact is None and engine.processed_count == 0


def test_independent_interleaved_analyzers_have_no_shared_state():
    a, b = analyzer(), analyzer()
    for f in upstream(base()):
        assert a.update(f) == b.update(f)


@pytest.mark.parametrize("bad", [None, {}, "frame", 1, bar(0, 100)])
def test_untyped_or_raw_input_fails_before_state_mutation(bad):
    raw = upstream(base())
    engine = analyzer()
    before = tuple(engine.update(f) for f in raw[:8])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(bad)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (*before, engine.update(raw[8])) == analyze_mss(raw[:9])


@pytest.mark.parametrize("indices", [(1,), (0, 0), (0, 2)])
def test_missing_duplicate_or_shifted_indices_are_not_repaired(indices):
    raw = upstream(base())
    engine = analyzer()
    for index in indices[:-1]:
        engine.update(raw[index])
    state = (engine.latest, engine.processed_count)
    with pytest.raises(AnalysisInputError):
        engine.update(raw[indices[-1]])
    assert (engine.latest, engine.processed_count) == state


@pytest.mark.parametrize(
    "change", ["series", "analysis", "displacement", "fvg", "ob", "pd", "unit", "history"]
)
def test_conflicting_upstream_histories_and_configs_are_rejected(change):
    candles = base()
    raw = upstream(candles)
    kwargs = {
        "series": {"series": replace(SERIES, dataset_id="other-origin")},
        "analysis": {"analysis": AnalysisConfig(5)},
        "displacement": {"displacement": DisplacementConfig(atr_period=2)},
        "fvg": {"fvg": FVGConfig(min_gap_size=Decimal("0.1"))},
        "ob": {"order_blocks": OrderBlockConfig(require_fvg=True)},
        "pd": {"pd": PDConfig(Decimal("0.1"))},
        "unit": {"liquidity": LiquidityConfig("BTC")},
        "history": {},
    }[change]
    alternate = (
        (replace(candles[0], volume=Decimal(9)), *candles[1:]) if change == "history" else candles
    )
    other = upstream(alternate, **kwargs)
    engine = analyzer()
    first = engine.update(raw[0])
    with pytest.raises(AnalysisInputError):
        engine.update(other[1])
    assert engine.latest is first and engine.processed_count == 1


def test_visible_liquidity_configuration_cannot_switch():
    raw = upstream(base())
    changed = upstream(base(), liquidity=LiquidityConfig("USDT", Decimal(1)))
    engine = analyzer()
    for f in raw[:3]:
        engine.update(f)
    with pytest.raises(AnalysisInputError, match="liquidity configuration"):
        engine.update(changed[3])


def test_observed_time_gaps_do_not_create_synthetic_confirmations():
    candles = tuple(
        replace(c, timestamp=c.timestamp + timedelta(days=2 if i >= 5 else 0))
        for i, c in enumerate(base())
    )
    frames = run(candles)
    assert [e.detection_index for e in events(frames)] == [8, 13]
    assert frames[4].upstream.observation.reference.closed_at == base()[4].timestamp + timedelta(
        minutes=15
    )


def test_default_long_example_is_stable_under_replay():
    assert run(golden(), displacement=DisplacementConfig()) == run(
        golden(), displacement=DisplacementConfig()
    )


def test_existing_data_schema_still_rejects_missing_invalid_values():
    for values in (
        {"close": Decimal("NaN")},
        {"low": None},
        {"high": Decimal(1)},
        {"volume": Decimal(-1)},
    ):
        with pytest.raises(DataValidationError):
            replace(base()[0], **values)


def test_no_noniterable_batch_fallback():
    with pytest.raises(AnalysisInputError):
        analyze_mss(None)
