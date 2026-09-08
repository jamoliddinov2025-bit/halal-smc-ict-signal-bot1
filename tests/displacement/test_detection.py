from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.displacement import DisplacementConfig, SweepContext, analyze_displacement
from tests.analysis.helpers import bar
from tests.displacement.helpers import bear, bull, events, golden, run, upstream, warmup


@pytest.mark.parametrize(
    "candidate,direction,location",
    [(bull(), TrendDirection.BULLISH, "0.70"), (bear(), TrendDirection.BEARISH, "0.30")],
)
def test_all_default_threshold_boundaries_are_inclusive(candidate, direction, location):
    frames = run((*warmup(), candidate))
    (event,) = events(frames)
    assert event.direction == direction
    assert event.body_size == 2 and event.range_size == 3
    assert event.body_atr_ratio == 1 and event.range_atr_ratio == Decimal("1.5")
    assert event.close_location_ratio == Decimal(location)
    assert event.start_index == event.end_index == event.detection_index == 4
    assert event.timestamp == candidate.timestamp
    assert event.available_at == frames[4].liquidity.context.observation.reference.closed_at
    assert event.symbol == "BTCUSDT" and event.timeframe == "15m" and event.price_unit == "USDT"
    assert event.sweep_context == SweepContext.NONE and event.preceding_sweeps == ()


def test_default_fourteen_period_fixture_has_two_consecutive_events():
    frames = analyze_displacement(upstream(golden()), DisplacementConfig(), price_unit="USDT")
    assert [(e.detection_index, e.direction.value) for e in events(frames)] == [
        (15, "bullish"),
        (16, "bearish"),
    ]
    assert frames[14].atr_reference is None
    assert frames[14].atr_current.value == 2
    assert frames[15].atr_reference is frames[14].atr_current
    assert frames[16].atr_reference.total_true_range == 31
    assert all(e.sweep_context == SweepContext.NONE for e in events(frames))


@pytest.mark.parametrize(
    "opening,expected", [("106", True), ("106.00000001", False), ("105.99999999", True)]
)
def test_body_threshold_boundary_with_sufficient_range(opening, expected):
    candidate = bar(4, 108, opening=opening, high=110, low=100)
    assert bool(events(run((*warmup(), candidate)))) == expected


@pytest.mark.parametrize(
    "high,expected", [("103", True), ("102.99999999", False), ("103.00000001", True)]
)
def test_range_threshold_boundary_with_sufficient_body(high, expected):
    candidate = bar(4, "102.7", opening=100, high=high, low=100)
    assert bool(events(run((*warmup(), candidate)))) == expected


@pytest.mark.parametrize(
    "close,expected", [("107", True), ("106.999999999", False), ("107.000000001", True)]
)
def test_bullish_close_location_boundary(close, expected):
    candidate = bar(4, close, opening=102, high=110, low=100)
    assert bool(events(run((*warmup(), candidate)))) == expected


@pytest.mark.parametrize(
    "close,expected", [("103", True), ("103.000000001", False), ("102.999999999", True)]
)
def test_bearish_close_location_boundary(close, expected):
    candidate = bar(4, close, opening=108, high=110, low=100)
    assert bool(events(run((*warmup(), candidate)))) == expected


def test_rounded_close_ratio_is_never_used_to_accept_a_below_boundary_close():
    close = Decimal("106." + "9" * 70)
    frame = run((*warmup(), bar(4, close, opening=102, high=110, low=100)))[-1]
    assert frame.metrics.close_location_ratio == Decimal("0.70")  # display rounding only
    assert not frame.events  # exact comparison remains below the threshold


def test_large_body_but_insufficient_total_range_is_not_displacement():
    frame = run((*warmup(), bar(4, "102.5", opening=100, high="102.5", low=100)))[-1]
    assert frame.metrics.body_size > 2
    assert frame.metrics.range_atr_ratio < Decimal("1.5")
    assert not frame.events


def test_large_range_long_wicks_but_small_body_are_not_displacement():
    frame = run((*warmup(), bar(4, "100.1", opening=100, high=110, low=90)))[-1]
    assert frame.metrics.range_size == 20 and frame.metrics.body_size == Decimal("0.1")
    assert not frame.events


def test_long_wick_is_not_an_undocumented_extra_filter():
    # The four declared rules pass; no hidden body/range dominance heuristic exists.
    assert events(run((*warmup(), bar(4, 105, opening=100, high=105, low=50))))


def test_doji_is_nondirectional_even_with_a_large_range():
    frame = run((*warmup(), bar(4, 100, opening=100, high=120, low=80)))[-1]
    assert frame.metrics.direction is None and frame.metrics.body_size == 0
    assert not frame.events


def test_zero_range_has_no_close_location_and_no_event():
    frame = run((*warmup(), bar(4, 100, opening=100, high=100, low=100)))[-1]
    assert frame.metrics.range_size == 0 and frame.metrics.close_location_ratio is None
    assert not frame.events


def test_gap_alone_does_not_substitute_for_body_or_range():
    frame = run((*warmup(), bar(4, 121, opening=120, high=122, low=119)))[-1]
    assert frame.metrics.body_size == 1 and frame.metrics.range_size == 3
    assert not frame.events
    assert frame.atr_current.true_ranges[-1] == 22


def test_gapped_candle_with_real_body_and_range_can_qualify():
    (event,) = events(run((*warmup(), bar(4, 125, opening=120, high=126, low=119))))
    assert event.body_size == 5 and event.range_size == 7


def test_direction_is_the_candle_direction_not_a_trend_or_sweep_filter():
    frame = run((*warmup(), bull()))[-1]
    assert frame.liquidity.context.snapshot.trend.direction == TrendDirection.RANGING
    assert frame.events[0].direction == TrendDirection.BULLISH


def test_tighter_configuration_changes_detection_not_raw_candle_facts():
    candles = (*warmup(), bull())
    loose = run(candles)[-1]
    tight = run(candles, min_body_atr="1.01")[-1]
    assert loose.metrics == tight.metrics
    assert loose.events and not tight.events
    assert loose.provenance.configuration_hash != tight.provenance.configuration_hash
    assert loose.atr_reference == tight.atr_reference


@pytest.mark.parametrize("exponent", [-40, -800])
def test_positive_near_zero_atr_is_scale_invariant_without_magic_epsilon(exponent):
    candles = tuple(
        replace(
            c,
            **{
                name: getattr(c, name).scaleb(exponent) for name in ("open", "high", "low", "close")
            },
        )
        for c in (*warmup(), bull())
    )
    (event,) = events(run(candles))
    assert event.atr_reference.value == Decimal(2).scaleb(exponent)
    assert event.body_atr_ratio == 1 and event.range_atr_ratio == Decimal("1.5")
    assert not events(run(candles, atr_floor=Decimal(2).scaleb(exponent)))
