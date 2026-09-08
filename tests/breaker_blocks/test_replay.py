from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, AnalysisInputError
from smcsignal.analysis.breaker_blocks import analyze_breaker_blocks
from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.fvg import FVGAnalyzer, FVGConfig
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquidityConfig
from smcsignal.analysis.mss import MSSAnalyzer
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer, OrderBlockConfig
from smcsignal.analysis.premium_discount import PDAnalyzer, PDConfig
from smcsignal.data import DataValidationError
from tests.analysis.helpers import bar
from tests.breaker_blocks.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    base,
    events,
    upstream,
)


def test_batch_stream_and_original_source_frames_are_identical():
    source = upstream(base())
    engine = analyzer()
    actual = tuple(engine.update(frame) for frame in source)
    assert actual == analyze_breaker_blocks(source)
    assert all(frame.upstream is original for frame, original in zip(actual, source, strict=True))
    assert engine.latest is actual[-1] and engine.processed_count == len(source)
    assert engine.series == SERIES and engine.price_unit == "USDT"


@pytest.mark.parametrize("cut", range(15))
def test_every_chunk_boundary_preserves_source_eligibility_and_conversion(cut):
    source = upstream(base())
    engine = analyzer()
    frames = []
    for chunk in (source[:cut], (), source[cut:]):
        frames.extend(engine.update(frame) for frame in chunk)
    assert tuple(frames) == analyze_breaker_blocks(source)


def test_no_duplicate_upstream_detectors_are_run_by_breaker_consumer():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = MSSAnalyzer()
    breaker = analyzer()
    frames = []
    for c in base():
        a = liquidity.update(c)
        b = displacement.update(a)
        d = blocks.update(gaps.update(b))
        frames.append(breaker.update(mss.update(pd.update(d))))
    assert tuple(frames) == analyze_breaker_blocks(upstream(base()))
    assert (
        liquidity.processed_count
        == displacement.processed_count
        == gaps.processed_count
        == blocks.processed_count
        == pd.processed_count
        == mss.processed_count
        == breaker.processed_count
        == len(base())
    )


def test_empty_and_single_pass_inputs():
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from upstream(base())

    assert analyze_breaker_blocks(Once()) == analyze_breaker_blocks(upstream(base()))
    assert analyze_breaker_blocks(()) == ()
    engine = analyzer()
    assert engine.latest is None and engine.configuration_artifact is None
    assert engine.series is None and engine.price_unit is None and engine.processed_count == 0


def test_independent_interleaved_analyzers_do_not_share_conversion_state():
    left, right = analyzer(), analyzer()
    for frame in upstream(base()):
        assert left.update(frame) == right.update(frame)


@pytest.mark.parametrize("bad", [None, {}, "frame", 0, bar(0, 100)])
def test_invalid_input_does_not_consume_ob_or_advance_state(bad):
    source = upstream(base())
    engine = analyzer()
    before = tuple(engine.update(frame) for frame in source[:8])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(bad)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (*before, engine.update(source[8])) == analyze_breaker_blocks(source[:9])


@pytest.mark.parametrize("indices", [(1,), (0, 0), (0, 2)])
def test_shifted_missing_duplicate_indices_are_errors(indices):
    source = upstream(base())
    engine = analyzer()
    for index in indices[:-1]:
        engine.update(source[index])
    state = (engine.latest, engine.processed_count)
    with pytest.raises(AnalysisInputError):
        engine.update(source[indices[-1]])
    assert (engine.latest, engine.processed_count) == state


@pytest.mark.parametrize(
    "change", ["series", "structure", "displacement", "fvg", "ob", "pd", "unit", "history"]
)
def test_conflicting_source_context_cannot_be_combined(change):
    candles = base()
    original = upstream(candles)
    kwargs = {
        "series": {"series": replace(SERIES, dataset_id="another-origin")},
        "structure": {"analysis": AnalysisConfig(5)},
        "displacement": {"displacement": DisplacementConfig(atr_period=2)},
        "fvg": {"fvg": FVGConfig(min_gap_size=Decimal("0.1"))},
        "ob": {"order_blocks": OrderBlockConfig(require_fvg=True)},
        "pd": {"pd": PDConfig(Decimal("0.1"))},
        "unit": {"liquidity": LiquidityConfig("BTC")},
        "history": {},
    }[change]
    revised = (
        (replace(candles[0], volume=Decimal(9)), *candles[1:]) if change == "history" else candles
    )
    other = upstream(revised, **kwargs)
    engine = analyzer()
    first = engine.update(original[0])
    with pytest.raises(AnalysisInputError):
        engine.update(other[1])
    assert engine.latest is first


def test_visible_liquidity_configuration_cannot_change():
    source = upstream(base())
    changed = upstream(base(), liquidity=LiquidityConfig("USDT", Decimal(1)))
    engine = analyzer()
    for frame in source[:3]:
        engine.update(frame)
    with pytest.raises(AnalysisInputError):
        engine.update(changed[3])


def test_time_gaps_do_not_invent_missing_ob_or_confirmation_candles():
    candles = tuple(
        replace(c, timestamp=c.timestamp + timedelta(days=2 if i >= 7 else 0))
        for i, c in enumerate(base())
    )
    actual = analyze_breaker_blocks(upstream(candles))
    assert [e.confirmation_index for e in events(actual)] == [8, 13]
    assert actual[6].upstream.upstream.observation.reference.closed_at == base()[
        6
    ].timestamp + timedelta(minutes=15)


def test_invalid_raw_values_remain_rejected_by_approved_data_models():
    for fields in (
        {"low": None},
        {"close": Decimal("NaN")},
        {"high": Decimal(1)},
        {"volume": Decimal(-1)},
    ):
        with pytest.raises(DataValidationError):
            replace(base()[0], **fields)


def test_noniterable_batch_fails():
    with pytest.raises(AnalysisInputError):
        analyze_breaker_blocks(None)
