from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis.liquidity import (
    InvalidationReason,
    LiquidityKind,
    LiquiditySide,
    PoolStatus,
)
from tests.analysis.helpers import bar, mirrored
from tests.liquidity.helpers import analyzer, golden, run, single_high, sweeps


def test_hand_computed_equal_high_and_equal_low_sweeps():
    frames = run(golden())
    events = sweeps(frames)
    assert [
        (
            e.breach.reference.candle_index,
            e.side,
            e.pool.kind,
            e.pool.reference_price,
            e.extreme_price,
            e.reclaim_close,
        )
        for e in events
    ] == [
        (
            9,
            LiquiditySide.BUY_SIDE,
            LiquidityKind.EQUAL_HIGHS,
            Decimal(16),
            Decimal(17),
            Decimal(12),
        ),
        (
            10,
            LiquiditySide.SELL_SIDE,
            LiquidityKind.EQUAL_LOWS,
            Decimal(9),
            Decimal(8),
            Decimal(12),
        ),
    ]
    for event in events:
        assert event.pool.status == PoolStatus.ACTIVE
        assert event.confirmed_at == event.breach.reference.closed_at
        assert event.pool.provenance.available_at <= event.breach.reference.opened_at
        retired = next(
            p
            for p in frames[event.breach.reference.candle_index].pool_updates
            if p.pool_id == event.pool.pool_id
        )
        assert retired.status == PoolStatus.SWEPT
        assert retired.previous_snapshot == event.pool.provenance.as_reference()
        assert retired.sweep_reference == event.provenance.as_reference()


@pytest.mark.parametrize("mirror", [False, True])
def test_single_swing_pool_can_be_swept(mirror):
    candles = single_high(bar(3, 12, opening=12, high=17, low=11), mirror=mirror)
    (event,) = sweeps(run(candles))
    assert event.side == (LiquiditySide.SELL_SIDE if mirror else LiquiditySide.BUY_SIDE)
    assert event.pool.touch_count == 1


@pytest.mark.parametrize("mirror", [False, True])
def test_exact_extreme_touch_is_not_a_breach(mirror):
    frames = run(single_high(bar(3, 12, opening=12, high=16, low=11), mirror=mirror))
    assert not sweeps(frames)
    assert not any(p.status != PoolStatus.ACTIVE for p in frames[3].pool_updates)


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("close", [16, 17])
def test_close_at_or_beyond_boundary_invalidates_without_sweep(mirror, close):
    frames = run(single_high(bar(3, close, opening=12, high=18, low=11), mirror=mirror))
    assert not sweeps(frames)
    terminal = next(p for p in frames[3].pool_updates if p.status == PoolStatus.INVALIDATED)
    assert terminal.invalidation_reason == InvalidationReason.NOT_RECLAIMED


@pytest.mark.parametrize("mirror", [False, True])
def test_gap_outside_band_is_not_labeled_a_sweep_even_if_it_returns(mirror):
    frames = run(single_high(bar(3, 12, opening=17, high=18, low=11), mirror=mirror))
    assert not sweeps(frames)
    assert any(
        p.invalidation_reason == InvalidationReason.STARTED_OUTSIDE for p in frames[3].pool_updates
    )


def test_close_inside_tolerance_band_is_not_full_reclaim():
    candles = single_high(bar(3, "15.90", opening=12, high=17, low=11))
    frames = run(candles, bps="100")
    assert not sweeps(frames)
    assert any(
        p.invalidation_reason == InvalidationReason.NOT_RECLAIMED for p in frames[3].pool_updates
    )


def test_a_later_return_does_not_relabel_a_failed_breach():
    failed = single_high(bar(3, 17, opening=12, high=18, low=11))
    frames = run((*failed, bar(4, 12, opening=17, high=17, low=11)))
    assert not sweeps(frames)
    assert frames[3].pool_updates[0].status == PoolStatus.INVALIDATED


def test_simultaneous_both_side_sweeps_are_retained_without_order_claim():
    candles = (*golden()[:4], bar(4, 12, opening=12, high=17, low=10))
    events = run(candles)[4].sweeps
    assert [e.side for e in events] == [LiquiditySide.BUY_SIDE, LiquiditySide.SELL_SIDE]
    assert all(e.breach.reference.candle_index == 4 for e in events)


def test_one_candle_can_sweep_multiple_distinct_pools_once_each():
    candles = tuple(bar(i, 90, high=h, low=89) for i, h in enumerate([91, 100, 91, 95, 91]))
    frames = run(
        (
            *candles,
            bar(5, 90, opening=90, high=101, low=89),
            bar(6, 90, opening=90, high=102, low=89),
        )
    )
    assert [e.pool.reference_price for e in frames[5].sweeps] == [100, 95]
    assert not frames[6].sweeps
    assert len({e.pool.pool_id for e in sweeps(frames)}) == len(sweeps(frames))


def test_new_confirmation_cannot_change_sweep_target_on_same_candle():
    frames = run(golden())
    # At 10 a new high is confirmed; the sell sweep still targets the old EQL at 9.
    (event,) = frames[10].sweeps
    assert event.pool.provenance == frames[8].pool_updates[0].provenance
    assert any(
        p.reference_price == 17 and p.status == PoolStatus.ACTIVE for p in frames[10].pool_updates
    )
    assert event.pool.members[-1].swing.confirmed_index == 8


def test_delayed_pool_not_known_at_bar_open_is_not_backdated():
    engine = analyzer()
    candles = single_high(bar(3, 12, opening=12, high=17, low=11))
    for c in candles[:2]:
        engine.update(c)
    late = candles[3].timestamp + timedelta(microseconds=1)
    pool_frame = engine.update(candles[2], available_at=late)
    assert pool_frame.pool_updates[0].provenance.available_at == late
    frame = engine.update(candles[3])
    assert not frame.sweeps
    assert frame.pool_updates[0].invalidation_reason == InvalidationReason.NOT_KNOWN_AT_OPEN


def test_delayed_current_arrival_preserves_actual_confirmed_time():
    engine = analyzer()
    candles = single_high(bar(3, 12, opening=12, high=17, low=11))
    for c in candles[:3]:
        engine.update(c)
    delayed = candles[3].timestamp + timedelta(minutes=16, microseconds=1)
    (event,) = engine.update(candles[3], available_at=delayed).sweeps
    assert event.confirmed_at == delayed
    assert event.confirmed_at > event.breach.reference.closed_at


def test_mirrored_golden_sweeps_have_opposite_sides():
    events = sweeps(run(mirrored(golden())))
    assert [e.side for e in events] == [LiquiditySide.SELL_SIDE, LiquiditySide.BUY_SIDE]
    assert [e.breach.reference.candle_index for e in events] == [9, 10]


def test_event_keeps_actual_pre_and_post_structure_context():
    frames = run(golden())
    for event in sweeps(frames):
        index = event.breach.reference.candle_index
        assert event.context_before == frames[index - 1].context
        assert event.context == frames[index].context
        assert event.pool.context.provenance.available_at < event.confirmed_at


def test_later_new_swing_at_same_price_gets_new_entity_not_resurrection():
    candles = (
        *single_high(bar(3, 12, opening=12, high=17, low=11)),
        bar(4, 12),
        bar(5, 12, high=16, low=11),
        bar(6, 12),
        bar(7, 12, high=17, low=11),
        bar(8, 12),
    )
    events = sweeps(run(candles))
    assert [e.breach.reference.candle_index for e in events] == [3, 7]
    assert [e.pool.reference_price for e in events] == [16, 16]
    assert events[0].pool.pool_id != events[1].pool.pool_id
    assert [e.pool.members[0].swing.pivot_index for e in events] == [1, 5]
