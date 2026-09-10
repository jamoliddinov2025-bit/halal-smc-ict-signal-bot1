from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfig, AnalysisInputError
from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.fvg import FVGAnalyzer, FVGConfig
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquidityConfig
from smcsignal.analysis.order_blocks import (
    OrderBlockConfig,
    StructureRequirement,
    analyze_order_blocks,
)
from smcsignal.data import DataValidationError
from tests.analysis.helpers import bar
from tests.order_blocks.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    bullish,
    events,
    golden,
    run,
    simple,
    upstream,
)


def test_batch_stream_and_original_frame_identity():
    source = upstream(golden(), displacement=DisplacementConfig())
    detector = analyzer()
    actual = tuple(detector.update(frame) for frame in source)
    assert actual == analyze_order_blocks(source)
    assert all(frame.upstream is original for frame, original in zip(actual, source, strict=True))
    assert detector.latest is actual[-1] and detector.processed_count == len(source)
    assert detector.series == SERIES and detector.price_unit == "USDT"


@pytest.mark.parametrize("cut", range(9))
@pytest.mark.parametrize("require_fvg", [False, True])
def test_every_chunk_boundary_preserves_pending_and_published_results(cut, require_fvg):
    source = upstream(bullish())
    detector = analyzer(require_fvg=require_fvg)
    output = []
    for chunk in (source[:cut], (), source[cut:]):
        output.extend(detector.update(frame) for frame in chunk)
    assert tuple(output) == analyze_order_blocks(source, OrderBlockConfig(require_fvg=require_fvg))


def test_existing_upstream_pipeline_is_run_once_per_candle():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    detector = analyzer(require_fvg=True)
    result = tuple(
        detector.update(fvg.update(displacement.update(liquidity.update(c)))) for c in bullish()
    )
    assert result == run(bullish(), require_fvg=True)
    assert (
        liquidity.processed_count
        == displacement.processed_count
        == fvg.processed_count
        == detector.processed_count
        == 8
    )


def test_empty_and_single_pass_iterables():
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from upstream(bullish())

    assert analyze_order_blocks(Once()) == run(bullish())
    assert analyze_order_blocks(()) == ()
    detector = analyzer()
    assert (
        detector.latest is None
        and detector.series is None
        and detector.configuration_artifact is None
    )
    assert detector.price_unit is None and detector.processed_count == 0


def test_independent_instances_do_not_share_pending_state():
    left, right = analyzer(require_fvg=True), analyzer(require_fvg=True)
    for frame in upstream(bullish()):
        assert left.update(frame) == right.update(frame)


@pytest.mark.parametrize("bad", [None, {}, "data", 1, bar(0, 100)])
def test_invalid_or_raw_input_is_atomic(bad):
    source = upstream(bullish())
    detector = analyzer(require_fvg=True)
    prefix = tuple(detector.update(frame) for frame in source[:7])
    state = (detector.latest, detector.processed_count, detector.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        detector.update(bad)
    assert (detector.latest, detector.processed_count, detector.configuration_artifact) == state
    assert (*prefix, detector.update(source[7])) == run(bullish(), require_fvg=True)


@pytest.mark.parametrize("indices", [(1,), (0, 0), (0, 2)])
def test_missing_duplicate_shifted_indices_are_not_repaired(indices):
    source = upstream(bullish())
    detector = analyzer()
    for index in indices[:-1]:
        detector.update(source[index])
    state = (detector.latest, detector.processed_count)
    with pytest.raises(AnalysisInputError):
        detector.update(source[indices[-1]])
    assert (detector.latest, detector.processed_count) == state


@pytest.mark.parametrize("change", ["series", "analysis", "displacement", "fvg", "unit", "history"])
def test_conflicting_upstream_contexts_cannot_be_mixed(change):
    candles = bullish()
    source = upstream(candles)
    kwargs = {
        "series": {"series": replace(SERIES, dataset_id="other-origin")},
        "analysis": {"analysis": AnalysisConfig(5)},
        "displacement": {"displacement": DisplacementConfig(atr_period=2)},
        "fvg": {"fvg": FVGConfig(min_gap_size=Decimal("0.1"))},
        "unit": {"liquidity": LiquidityConfig("BTC")},
        "history": {},
    }[change]
    altered = (
        (replace(candles[0], volume=Decimal(9)), *candles[1:]) if change == "history" else candles
    )
    other = upstream(altered, **kwargs)
    detector = analyzer()
    first = detector.update(source[0])
    with pytest.raises(AnalysisInputError):
        detector.update(other[1])
    assert detector.latest is first and detector.processed_count == 1


def test_visible_liquidity_configuration_is_bound():
    source = upstream(bullish())
    other = upstream(bullish(), liquidity=LiquidityConfig("USDT", Decimal(1)))
    detector = analyzer()
    for f in source[:3]:
        detector.update(f)
    with pytest.raises(AnalysisInputError, match="liquidity configuration"):
        detector.update(other[3])


def test_observed_time_gaps_do_not_change_lookback_indices_or_create_time_slots():
    candles = bullish()
    gapped = tuple(
        replace(c, timestamp=c.timestamp + timedelta(days=2 if i >= 4 else 0))
        for i, c in enumerate(candles)
    )
    (event,) = events(run(gapped))
    assert event.candidate_index == 4 and event.displacement_index == 6
    assert event.candidate_distance == 2


def test_delayed_publication_retains_actual_fvg_availability():
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    detector = analyzer(require_fvg=True)
    output = []
    for i, c in enumerate(bullish()):
        known = c.timestamp + timedelta(minutes=17, microseconds=1) if i == 7 else None
        output.append(
            detector.update(fvg.update(displacement.update(source.update(c, available_at=known))))
        )
    (event,) = output[7].events
    assert event.available_at == event.associated_fvg.available_at
    assert event.available_at > event.publication.upstream.metrics.observation.reference.closed_at


def test_invalid_candle_values_remain_rejected_by_existing_data_layer():
    for invalid in (
        {"close": Decimal("NaN")},
        {"low": None},
        {"volume": Decimal(-1)},
        {"high": Decimal(1)},
    ):
        with pytest.raises(DataValidationError):
            replace(bullish()[0], **invalid)


def test_identical_nested_and_overlapping_zones_are_independent_formations():
    candles = (
        *simple(),
        bar(5, 116, opening=105, high=117, low=104),
        bar(6, "100.2", opening="100.8", high="100.9", low="100.1"),
        bar(7, 139, opening="100.2", high=140, low=100),
        bar(8, "101.2", opening="101.8", high=102, low="100.5"),
        bar(9, 169, opening="101.2", high=170, low=100),
    )
    found = events(
        run(
            candles,
            displacement=DisplacementConfig(atr_period=1),
            structure_requirement=StructureRequirement.DISPLACEMENT_ONLY,
        )
    )
    assert [e.confirmation_index for e in found] == [4, 5, 7, 9]
    assert [(e.zone_lower_boundary, e.zone_upper_boundary) for e in found] == [
        (99, 101),
        (99, 101),
        (Decimal("100.1"), Decimal("100.9")),
        (Decimal("100.5"), 102),
    ]
    assert found[0].candidate_index == found[1].candidate_index == 3
    assert len({e.event_id for e in found}) == 4


def test_no_noniterable_batch_fallback():
    with pytest.raises(AnalysisInputError):
        analyze_order_blocks(None)
