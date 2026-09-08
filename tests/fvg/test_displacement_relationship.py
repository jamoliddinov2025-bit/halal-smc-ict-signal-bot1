from datetime import timedelta

import pytest

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from tests.analysis.helpers import bar, mirrored
from tests.fvg.helpers import (
    ANALYSIS,
    LIQUIDITY,
    SERIES,
    analyzer,
    associated,
    bullish,
    events,
    run,
    upstream,
)


@pytest.mark.parametrize("require", [False, True])
@pytest.mark.parametrize("mirror", [False, True])
def test_matching_middle_displacement_is_the_original_immutable_event(require, mirror):
    candles = associated()
    if mirror:
        candles = mirrored(candles)
    source = upstream(candles, displacement=DisplacementConfig(atr_period=1))
    detector = analyzer(require=require)
    frames = tuple(detector.update(f) for f in source)
    event = frames[3].events[0]
    assert event.associated_displacement is source[2].events[0]
    assert event.displacement_reference == source[2].events[0].provenance.as_reference()
    assert event.displacement_aligned is True
    assert event.direction == (TrendDirection.BEARISH if mirror else TrendDirection.BULLISH)
    assert event.associated_displacement.available_at <= event.available_at


def test_displacement_is_optional_by_default_and_missing_when_warmup_incomplete():
    assert events(run(bullish()))[0].associated_displacement is None
    assert not events(run(bullish(), require=True))


def test_opposing_displacement_is_preserved_not_relabelled_when_filter_is_off():
    event = run(associated(opposed=True), displacement=DisplacementConfig(atr_period=1))[3].events[
        0
    ]
    assert event.direction == TrendDirection.BULLISH
    assert event.associated_displacement.direction == TrendDirection.BEARISH
    assert event.displacement_aligned is False
    assert not run(
        associated(opposed=True), require=True, displacement=DisplacementConfig(atr_period=1)
    )[3].events


def test_c3_displacement_is_not_used_as_middle_displacement():
    candles = (
        bar(0, 100, opening=100, high=101, low=99),
        bar(1, 100, opening=100, high=101, low=99),
        bar(2, 100, opening=100, high=101, low=99),
        bar(3, 110, opening=103, high=111, low=102),
    )
    source = upstream(candles, displacement=DisplacementConfig(atr_period=1))
    assert not source[2].events and source[3].events
    detector = analyzer()
    frames = tuple(detector.update(f) for f in source)
    assert frames[3].events[0].associated_displacement is None
    assert not run(candles, require=True, displacement=DisplacementConfig(atr_period=1))[3].events


def test_delayed_middle_event_is_linked_only_at_actual_c3_availability():
    candles = associated()
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DisplacementConfig(atr_period=1), price_unit="USDT")
    fvg = analyzer(require=True)
    results = []
    for index, candle in enumerate(candles):
        # C2 becomes known during C3, but before its actual closed-candle assessment.
        delay = candles[3].timestamp + timedelta(minutes=1) if index == 2 else None
        results.append(fvg.update(displacement.update(source.update(candle, available_at=delay))))
    event = results[3].events[0]
    assert (
        event.c3.reference.opened_at
        < event.associated_displacement.available_at
        < event.available_at
    )


def test_same_clock_batched_arrival_still_uses_an_earlier_processed_c2_event():
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DisplacementConfig(atr_period=1), price_unit="USDT")
    detector = analyzer()
    candles = associated()
    known_at = candles[-1].timestamp + timedelta(hours=1)
    frames = tuple(
        detector.update(displacement.update(source.update(c, available_at=known_at)))
        for c in candles
    )
    event = frames[3].events[0]
    assert event.associated_displacement.available_at == event.available_at == known_at
    assert event.associated_displacement.detection_index < event.detection_index
