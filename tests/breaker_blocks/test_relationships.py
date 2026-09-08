from datetime import timedelta
from hashlib import sha256

from smcsignal.analysis import StructureEventKind
from smcsignal.analysis.breaker_blocks import BreakerRejection
from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquiditySide, evidence_json
from smcsignal.analysis.mss import MSSAnalyzer
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer
from smcsignal.analysis.premium_discount import PDAnalyzer
from tests.analysis.helpers import bar
from tests.breaker_blocks.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    base,
    events,
    run,
    upstream,
)
from tests.order_blocks.helpers import bullish, choch


def with_sweep():
    candles = list(base()[:9])
    # The new high at pivot 6 is confirmed by 7; an older still-active high is swept
    # on a later candle before the closing invalidation. Keep the source OB intact.
    candles[7] = bar(7, 19, opening=18, high=21, low=18)
    return (
        *candles[:8],
        bar(8, 19, opening=19, high=23, low=18),
        bar(9, 9, opening=19, high=20, low=8),
    )


def test_original_ob_candidate_confirmation_zone_and_id_are_exact_objects():
    source = upstream(choch())
    origin = source[6].upstream.upstream.events[0]
    before = evidence_json(origin)
    engine = analyzer()
    frames = tuple(engine.update(f) for f in source)
    event = frames[8].events[0]
    assert event.evidence.original_ob is origin
    assert event.evidence.original_ob_reference == origin.provenance.as_reference()
    assert event.original_candidate_index == origin.candidate_index
    assert event.original_ob_available_at == origin.available_at
    assert (
        event.lower_boundary == origin.zone_lower_boundary
        and event.upper_boundary == origin.zone_upper_boundary
    )
    assert evidence_json(origin) == before


def test_matching_mss_displacement_and_original_structure_are_retained():
    event = events(run(choch()))[0]
    proof = event.evidence
    assert proof.mss.evidence.displacement is proof.displacement
    assert proof.mss_reference == proof.mss.provenance.as_reference()
    assert proof.displacement_reference == proof.displacement.provenance.as_reference()
    assert proof.structure_events[0].kind == StructureEventKind.CHOCH
    assert proof.structure_events[0] is proof.mss.evidence.structure_break
    assert proof.structure_reference == proof.mss.evidence.structure_reference


def test_preceding_sweep_is_the_existing_mss_displacement_context():
    frames = run(with_sweep())
    event = frames[9].events[0]
    assert event.evidence.preceding_sweeps
    assert event.evidence.preceding_sweeps is event.evidence.displacement.preceding_sweeps
    assert event.evidence.preceding_sweeps[0].side == LiquiditySide.BUY_SIDE
    assert all(
        s.breach.reference.candle_index < event.invalidation_index
        for s in event.evidence.preceding_sweeps
    )


def test_same_candle_sweep_is_not_retrospectively_preceding():
    candles = (*bullish()[:7], bar(7, 12, opening=20, high=23, low=10))
    frame = run(candles)[7]
    assert frame.events
    assert frame.upstream.upstream.upstream.upstream.upstream.liquidity.sweeps
    assert frame.events[0].evidence.preceding_sweeps == ()


def test_concurrent_fvg_context_is_copied_without_claiming_future_c3():
    frame = run(base())[13]
    event = frame.events[0]
    assert event.evidence.concurrent_fvgs is event.evidence.mss.evidence.concurrent_fvgs
    assert event.evidence.concurrent_fvgs
    for gap in event.evidence.concurrent_fvgs:
        assert gap.detection_index == event.confirmation_index
        assert gap.c2.reference.candle_index == event.displacement_index - 1
        assert gap.provenance.as_reference() in event.evidence.fvg_references


def test_pd_is_the_original_invalidation_time_classification_and_reference():
    frame = run(choch())[8]
    proof = frame.events[0].evidence
    assert proof.pd_reference == frame.upstream.upstream.provenance.as_reference()
    assert proof.pd_classification == frame.upstream.upstream.classification
    assert proof.current.upstream is frame.upstream.upstream


def test_future_sweep_fvg_pd_ob_and_mss_cannot_enrich_old_breaker():
    candles = choch()
    before = run(candles)
    saved = evidence_json(before[8].events[0])
    future = run(
        (*candles, bar(9, 30, opening=9, high=31, low=8), bar(10, 5, opening=30, high=32, low=4))
    )
    assert future[: len(candles)] == before
    assert evidence_json(future[8].events[0]) == saved


def test_original_object_serialization_and_ids_remain_unchanged_after_all_consumption():
    source = upstream(base())
    objects = []
    for frame in source:
        objects.extend(frame.upstream.upstream.events)
        objects.extend(frame.events)
        objects.extend(frame.evidence)
        objects.append(frame.upstream)
    old = {
        obj.provenance.evidence_id: sha256(evidence_json(obj).encode()).hexdigest()
        for obj in objects
    }
    engine = analyzer()
    for frame in source:
        engine.update(frame)
    assert all(
        old[obj.provenance.evidence_id] == sha256(evidence_json(obj).encode()).hexdigest()
        for obj in objects
    )


def test_source_ob_arriving_during_break_bar_is_not_backdated():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    ob = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = MSSAnalyzer()
    breaker = analyzer()
    candles = (*bullish()[:7], bar(7, 5, opening=12, high=12, low=4))
    frames = []
    for i, c in enumerate(candles):
        delay = candles[7].timestamp + timedelta(microseconds=1) if i == 6 else None
        a = liquidity.update(c, available_at=delay)
        b = displacement.update(a)
        d = ob.update(fvg.update(b))
        frames.append(breaker.update(mss.update(pd.update(d))))
    assert frames[7].evidence[0].rejection_reason == BreakerRejection.SOURCE_NOT_KNOWN_AT_OPEN
    assert not frames[7].events


def test_actual_breaker_availability_preserves_delayed_source_arrival():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    ob = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = MSSAnalyzer()
    breaker = analyzer()
    frames = []
    for i, c in enumerate(choch()):
        delay = c.timestamp + timedelta(minutes=16, microseconds=1) if i == 8 else None
        a = liquidity.update(c, available_at=delay)
        b = displacement.update(a)
        d = ob.update(fvg.update(b))
        frames.append(breaker.update(mss.update(pd.update(d))))
    event = frames[8].events[0]
    assert (
        event.available_at
        == event.invalidation_available_at
        == event.displacement_available_at
        == event.mss_available_at
    )
    assert event.available_at > event.evidence.invalidating_candle.reference.closed_at
