from datetime import timedelta

import pytest

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.displacement import SweepContext
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquiditySide
from tests.analysis.helpers import bar
from tests.displacement.helpers import ANALYSIS, LIQUIDITY, SERIES, analyzer, events, run
from tests.liquidity.helpers import golden as liquidity_golden


def after_sell_candles():
    return (*liquidity_golden(), bar(12, 22, opening=12, high=23, low=11))


def test_displacement_after_previously_known_sell_side_sweep():
    frames = run(after_sell_candles())
    event = frames[12].events[0]
    assert event.direction == TrendDirection.BULLISH
    assert event.sweep_context == SweepContext.AFTER_SELL_SIDE
    assert event.preceding_sweeps == frames[10].liquidity.sweeps
    assert event.preceding_sweep_references == tuple(
        e.provenance.as_reference() for e in frames[10].liquidity.sweeps
    )
    assert all(ref in event.provenance.dependencies for ref in event.preceding_sweep_references)


def test_displacement_after_previously_known_buy_side_sweep():
    frames = run((*liquidity_golden()[:10], bar(10, 3, opening=12, high=13, low=2)))
    event = frames[10].events[0]
    assert event.direction == TrendDirection.BEARISH
    assert event.sweep_context == SweepContext.AFTER_BUY_SIDE
    assert event.preceding_sweeps == frames[9].liquidity.sweeps


def test_direction_neutral_association_does_not_force_a_raid_reversal_strategy():
    # Bullish candle following buy-side sweep still qualifies; its own sell sweep is not prior.
    frames = run((*liquidity_golden()[:10], bar(10, 22, opening=12, high=23, low=8)))
    event = frames[10].events[0]
    assert (
        event.direction == TrendDirection.BULLISH
        and event.sweep_context == SweepContext.AFTER_BUY_SIDE
    )
    assert any(s.side == LiquiditySide.SELL_SIDE for s in frames[10].liquidity.sweeps)
    assert all(s.breach.reference.candle_index == 9 for s in event.preceding_sweeps)


def test_same_candle_sweep_is_preserved_upstream_but_is_not_a_preceding_sweep():
    frames = run((*liquidity_golden()[:9], bar(9, 2, opening=12, high=17, low=1)))
    assert frames[9].liquidity.sweeps
    event = frames[9].events[0]
    assert event.sweep_context == SweepContext.NONE and not event.preceding_sweeps
    assert (
        frames[9].liquidity.sweeps[0].provenance.as_reference() not in event.provenance.dependencies
    )


@pytest.mark.parametrize(
    "lookback,relation",
    [
        (0, SweepContext.NONE),
        (1, SweepContext.NONE),
        (2, SweepContext.AFTER_SELL_SIDE),
        (20, SweepContext.AFTER_SELL_SIDE),
    ],
)
def test_context_window_boundary_and_disable_option(lookback, relation):
    event = run(after_sell_candles(), sweep_lookback_bars=lookback)[12].events[0]
    assert event.sweep_context == relation


def test_most_recent_eligible_cohort_is_used_not_an_older_opposite_side():
    frames = run(after_sell_candles())
    event = frames[12].events[0]
    assert frames[9].liquidity.sweeps[0].side == LiquiditySide.BUY_SIDE
    assert [s.breach.reference.candle_index for s in event.preceding_sweeps] == [10]


def test_both_sides_of_same_prior_candle_are_retained_without_arbitrary_selection():
    candles = (
        *liquidity_golden()[:4],
        bar(4, 12, opening=12, high=17, low=10),
        bar(5, 26, opening=12, high=27, low=11),
    )
    frames = run(candles)
    event = frames[5].events[0]
    assert event.sweep_context == SweepContext.AFTER_BOTH
    assert event.preceding_sweeps == frames[4].liquidity.sweeps
    assert len(event.preceding_sweep_references) == 2


def test_all_same_side_targets_on_latest_sweep_candle_are_retained():
    candles = tuple(
        bar(i, 90, opening=90, high=h, low=89) for i, h in enumerate([91, 100, 91, 95, 91, 101])
    )
    frames = run((*candles, bar(6, 112, opening=90, high=113, low=89)))
    event = frames[6].events[0]
    assert len(event.preceding_sweeps) == 2
    assert event.sweep_context == SweepContext.AFTER_BUY_SIDE
    assert event.preceding_sweeps == frames[5].liquidity.sweeps


def test_arrival_during_candidate_bar_cannot_be_backdated_into_preceding_context():
    candles = (
        *liquidity_golden()[:9],
        bar(9, 2, opening=12, high=17, low=1),
        bar(10, 31, opening=2, high=32, low=1),
        bar(11, 2, opening=31, high=32, low=1),
    )
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    detector = analyzer()
    result = []
    for index, candle in enumerate(candles):
        delayed = candles[10].timestamp + timedelta(microseconds=1) if index == 9 else None
        result.append(detector.update(source.update(candle, available_at=delayed)))
    assert result[9].liquidity.sweeps
    assert result[10].events[0].sweep_context == SweepContext.NONE
    assert result[11].events[0].sweep_context == SweepContext.AFTER_BUY_SIDE
    assert result[11].events[0].preceding_sweeps == result[9].liquidity.sweeps


def test_context_is_not_consumed_by_a_displacement_event():
    # No strategy-specific one-entry-per-raid suppression is implemented.
    candles = (
        *liquidity_golden()[:10],
        bar(10, 22, opening=12, high=23, low=11),
        bar(11, 4, opening=22, high=23, low=3),
    )
    frames = run(candles)
    assert len(events(frames[-2:])) == 2
    assert frames[10].events[0].preceding_sweeps == frames[11].events[0].preceding_sweeps
