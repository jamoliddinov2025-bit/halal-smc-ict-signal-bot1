from dataclasses import replace
from datetime import timedelta

import pytest

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquiditySide
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer, OrderBlockConfig
from smcsignal.analysis.premium_discount import PDAnalyzer, PDClassification
from tests.analysis.helpers import bar, mirrored
from tests.mss.helpers import ANALYSIS, DISPLACEMENT, LIQUIDITY, SERIES, analyzer, base, events, run


def after_sweep():
    candles = list(base()[:9])
    candles[6] = bar(6, "18.5", opening=17, high=21, low=17)
    return tuple(candles)


@pytest.mark.parametrize("mirror", [False, True])
def test_actual_preceding_sweep_and_target_pool_references_are_preserved(mirror):
    candles = mirrored(after_sweep()) if mirror else after_sweep()
    frame = run(candles)[8]
    proof = frame.events[0].evidence
    assert proof.preceding_sweeps is proof.displacement.preceding_sweeps
    assert [(s.breach.reference.candle_index, s.side) for s in proof.preceding_sweeps] == [
        (6, LiquiditySide.SELL_SIDE if mirror else LiquiditySide.BUY_SIDE)
    ]
    assert proof.sweep_references == proof.displacement.preceding_sweep_references
    assert proof.swept_pools[0] is proof.preceding_sweeps[0].pool
    assert proof.swept_pools[0].provenance.as_reference() in proof.related_pool_references


def test_mss_without_preceding_sweep_is_valid():
    proof = run(base())[8].events[0].evidence
    assert proof.preceding_sweeps == () and proof.swept_pools == ()


def test_same_candle_sweep_is_not_added_as_a_precursor():
    candles = (*base()[:8], bar(8, 12, opening=17, high=17, low=10))
    frame = run(candles)[8]
    assert frame.upstream.upstream.upstream.upstream.liquidity.sweeps
    proof = frame.events[0].evidence
    assert proof.preceding_sweeps == ()
    assert proof.sweep_references == ()


def test_known_same_direction_current_fvg_is_concurrent_context_not_current_displacement_output():
    frames = run(base())
    for event in events(frames):
        proof = event.evidence
        assert proof.concurrent_fvgs
        for gap in proof.concurrent_fvgs:
            assert gap.direction == proof.displacement.direction
            assert gap.detection_index == event.detection_index
            assert gap.c2.reference.candle_index == event.detection_index - 1
            assert (
                gap.associated_displacement is None
                or gap.associated_displacement.event_id != proof.displacement.event_id
            )
            assert gap.provenance.as_reference() in proof.fvg_references


def test_current_ob_for_exact_mss_displacement_and_current_pd_are_retained():
    frames = run(base())
    for event in events(frames):
        proof = event.evidence
        assert proof.displacement_order_blocks
        assert all(
            block.displacement is proof.displacement for block in proof.displacement_order_blocks
        )
        assert proof.pd_reference == proof.current.provenance.as_reference()
        assert proof.pd_classification == proof.current.classification
        assert proof.pd_array_contexts
        assert all(a in proof.current.arrays for a in proof.pd_array_contexts)
        assert all(
            ref in proof.provenance.dependencies
            for ref in (*proof.fvg_references, *proof.order_block_references, proof.pd_reference)
        )


def test_no_matching_ob_does_not_suppress_mss():
    candles = list(base()[:9])
    candles[7] = replace(candles[7], open=candles[7].close)
    event = run(candles)[8].events[0]
    assert event.evidence.displacement_order_blocks == ()


def test_pd_insufficiency_is_context_not_a_second_structure_control_detector():
    from smcsignal.analysis.displacement import DisplacementConfig
    from tests.mss.helpers import consecutive

    frames = run(consecutive(), displacement=DisplacementConfig())
    assert frames[21].events[0].evidence.pd_classification == PDClassification.INSUFFICIENT_CONTEXT
    assert (
        frames[22].events[0].evidence.prior_structure_context.snapshot.trend.direction
        == TrendDirection.BULLISH
    )
    assert frames[22].events


def test_broken_level_pool_versions_are_not_synthesized_or_replaced_with_latest():
    proof = run(base())[8].events[0].evidence
    assert proof.broken_level_pools
    for pool in proof.broken_level_pools:
        assert any(m.swing == proof.structure_break.level for m in pool.members)
        assert pool in proof.current.upstream.upstream.upstream.liquidity.pool_updates
        assert pool.provenance.as_reference() in proof.related_pool_references


def test_later_fvg_or_delayed_ob_cannot_enrich_an_already_emitted_mss():
    candles = (*base()[:9], bar(9, 10, opening=11, high=11, low=8))
    frames = run(candles, order_blocks=OrderBlockConfig(require_fvg=True))
    event = frames[8].events[0]
    assert event.evidence.displacement_order_blocks == ()
    assert run(candles[:9], order_blocks=OrderBlockConfig(require_fvg=True)) == frames[:9]


def test_prior_context_arriving_during_break_bar_is_not_backdated():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = analyzer()
    candles = base()[:9]
    results = []
    for i, c in enumerate(candles):
        delayed = candles[8].timestamp + timedelta(microseconds=1) if i == 7 else None
        results.append(
            mss.update(
                pd.update(
                    blocks.update(
                        gaps.update(displacement.update(liquidity.update(c, available_at=delayed)))
                    )
                )
            )
        )
    assert results[8].upstream.upstream.upstream.upstream.events
    assert not results[8].events


def test_current_delayed_publication_retains_real_available_time():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = analyzer()
    results = []
    for i, c in enumerate(base()[:9]):
        delayed = c.timestamp + timedelta(minutes=16, microseconds=1) if i == 8 else None
        results.append(
            mss.update(
                pd.update(
                    blocks.update(
                        gaps.update(displacement.update(liquidity.update(c, available_at=delayed)))
                    )
                )
            )
        )
    event = results[8].events[0]
    assert event.available_at > event.evidence.current.observation.reference.closed_at
    assert event.available_at == event.evidence.displacement.available_at


def test_prior_matching_bos_is_retained_as_control_context_not_reused_as_current_break():
    from tests.order_blocks.helpers import bullish as bos_candles

    candles = (*bos_candles()[:7], bar(7, 9, opening=20, high=21, low=8))
    event = run(candles)[7].events[0]
    assert [e.kind.value for e in event.evidence.prior_control_events] == ["BOS"]
    assert event.evidence.prior_control_events[0].candle_index == 6
    assert event.evidence.structure_break.kind.value == "CHoCH"
    assert event.evidence.structure_break.candle_index == 7


def test_missing_fvg_and_delayed_ob_context_never_become_hidden_mss_requirements():
    from smcsignal.analysis.fvg import FVGConfig

    changed = run(
        base(),
        fvg=FVGConfig(require_displacement=True),
        order_blocks=OrderBlockConfig(require_fvg=True),
    )
    assert [e.detection_index for e in events(changed)] == [8, 13]
    assert all(
        not e.evidence.concurrent_fvgs and not e.evidence.displacement_order_blocks
        for e in events(changed)
    )
