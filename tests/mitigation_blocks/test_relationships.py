from datetime import timedelta
from hashlib import sha256

from smcsignal.analysis.breaker_blocks import BreakerBlockAnalyzer
from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer, evidence_json
from smcsignal.analysis.mss import MSSAnalyzer
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer
from smcsignal.analysis.premium_discount import PDAnalyzer
from tests.analysis.helpers import bar
from tests.mitigation_blocks.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    before_breaker,
    breaker_frames,
    bullish_mitigation,
    events,
    post_breaker,
    run,
)
from tests.order_blocks.helpers import bullish


def test_original_ob_zone_id_and_object_are_preserved():
    source = breaker_frames(bullish_mitigation())
    origin = source[6].upstream.upstream.upstream.events[0]
    before = evidence_json(origin)
    engine = analyzer()
    frames = tuple(engine.update(f) for f in source)
    event = frames[7].events[0]
    assert event.evidence.original_ob is origin
    assert event.evidence.original_ob_reference == origin.provenance.as_reference()
    assert event.original_candidate_index == origin.candidate_index
    assert event.original_ob_available_at == origin.available_at
    assert event.original_lower_boundary == origin.zone_lower_boundary
    assert event.original_upper_boundary == origin.zone_upper_boundary
    assert evidence_json(origin) == before


def test_mitigation_direction_matches_the_source_ob_not_an_opposing_breaker():
    event = events(run(bullish_mitigation()))[0]
    assert event.direction.value == event.evidence.original_ob.direction.value == "bullish"


def test_future_breaker_cannot_enrich_or_delete_a_prior_mitigation():
    candles = before_breaker()
    before = run(candles[:8])
    saved = evidence_json(before[7].events[0])
    future = run(candles)
    assert future[:8] == before
    assert evidence_json(future[7].events[0]) == saved
    assert future[9].upstream.events


def test_post_breaker_rejection_does_not_mutate_the_original_ob_or_breaker():
    source_run = run(post_breaker())
    origin = source_run[6].upstream.upstream.upstream.upstream.events[0]
    breaker = source_run[8].upstream.events[0]
    origin_json = evidence_json(origin)
    breaker_json = evidence_json(breaker)
    assert not events(source_run)
    assert evidence_json(source_run[6].upstream.upstream.upstream.upstream.events[0]) == origin_json
    assert evidence_json(source_run[8].upstream.events[0]) == breaker_json


def test_original_object_serialization_and_ids_remain_unchanged_after_consumption():
    source = breaker_frames(bullish_mitigation())
    objects = []
    for frame in source:
        objects.extend(frame.upstream.upstream.upstream.events)
        objects.extend(frame.events)
        objects.extend(frame.evidence)
        objects.append(frame)
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


def test_source_ob_arriving_during_the_interaction_bar_is_not_backdated():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    ob = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = MSSAnalyzer()
    breaker = BreakerBlockAnalyzer()
    mitigation = analyzer()
    candles = bullish_mitigation()
    frames = []
    for i, c in enumerate(candles):
        delay = candles[7].timestamp + timedelta(microseconds=1) if i == 6 else None
        a = liquidity.update(c, available_at=delay)
        b = displacement.update(a)
        d = ob.update(fvg.update(b))
        frames.append(mitigation.update(breaker.update(mss.update(pd.update(d)))))
    assert not frames[7].events
    assert frames[6].upstream.upstream.upstream.upstream.events


def test_actual_mitigation_availability_preserves_delayed_interaction_arrival():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    ob = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    mss = MSSAnalyzer()
    breaker = BreakerBlockAnalyzer()
    mitigation = analyzer()
    frames = []
    for i, c in enumerate(bullish_mitigation()):
        delay = c.timestamp + timedelta(minutes=16, microseconds=1) if i == 7 else None
        a = liquidity.update(c, available_at=delay)
        b = displacement.update(a)
        d = ob.update(fvg.update(b))
        frames.append(mitigation.update(breaker.update(mss.update(pd.update(d)))))
    event = frames[7].events[0]
    assert event.available_at == event.interaction_available_at
    assert event.available_at > event.evidence.interaction_candle.reference.closed_at
    assert event.interaction_timestamp == event.evidence.interaction_candle.reference.opened_at
    assert event.interaction_closed_at == event.evidence.interaction_candle.reference.closed_at


def test_five_timing_coordinates_remain_distinct_on_a_confirmed_record():
    event = events(run(bullish_mitigation()))[0]
    times = (
        event.original_candidate_timestamp,
        event.original_ob_available_at,
        event.interaction_timestamp,
        event.interaction_closed_at,
        event.available_at,
    )
    assert times == tuple(sorted(times))
    assert event.original_candidate_available_at <= event.original_ob_available_at
    assert event.interaction_available_at == event.available_at


def test_same_candle_new_ob_is_not_treated_as_a_pre_bar_source(monkeypatch=None):
    candles = (*bullish()[:7], bar(7, 5, opening=12, high=12, low=4))
    frames = run(candles)
    assert all(e.original_ob_confirmation_index < e.confirmation_index for e in events(frames))
