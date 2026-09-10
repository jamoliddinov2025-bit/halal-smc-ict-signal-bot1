from decimal import Decimal, localcontext

import pytest

from smcsignal.analysis import detect_swings
from smcsignal.analysis.liquidity import LiquidityKind, LiquiditySide, PoolStatus
from tests.analysis.helpers import bar, mirrored, series
from tests.liquidity.helpers import ANALYSIS, analyzer, golden, pool_updates, run


def test_first_confirmed_high_creates_buy_side_pool_only_at_confirmation():
    frames = run(golden())
    assert not frames[0].pool_updates and not frames[1].pool_updates
    pool = frames[2].pool_updates[0]
    assert pool.side == LiquiditySide.BUY_SIDE
    assert pool.kind == LiquidityKind.SWING_HIGH
    assert pool.reference_price == pool.lower_bound == pool.upper_bound == Decimal(16)
    assert pool.touch_count == 1
    assert pool.formation_start.reference.candle_index == 1
    assert pool.first_confirmed_at == frames[2].context.observation.reference.closed_at
    assert pool.members[0].swing == detect_swings(golden()[:3], ANALYSIS)[0]


def test_confirmed_low_creates_sell_side_pool():
    pool = run(golden())[3].pool_updates[0]
    assert pool.side == LiquiditySide.SELL_SIDE
    assert pool.kind == LiquidityKind.SWING_LOW
    assert pool.reference_price == Decimal(11)


def test_equal_highs_and_lows_are_membership_revisions_not_new_entities():
    frames = run(golden())
    high_single, high_equal = frames[2].pool_updates[0], frames[4].pool_updates[0]
    low_single, low_equal = frames[6].pool_updates[0], frames[8].pool_updates[0]
    for single, equal, kind, pivots in (
        (high_single, high_equal, LiquidityKind.EQUAL_HIGHS, [1, 3]),
        (low_single, low_equal, LiquidityKind.EQUAL_LOWS, [5, 7]),
    ):
        assert equal.pool_id == single.pool_id
        assert equal.provenance.evidence_id != single.provenance.evidence_id
        assert equal.previous_snapshot == single.provenance.as_reference()
        assert equal.kind == kind and equal.touch_count == 2
        assert [m.swing.pivot_index for m in equal.members] == pivots
        assert single.touch_count == 1
        assert equal.first_confirmed_at == single.first_confirmed_at


def test_mirrored_series_reverses_sides_without_changing_confirmation_indices():
    original, inverse = run(golden()), run(mirrored(golden()))
    for first, second in zip(original, inverse, strict=True):
        assert len(first.confirmed_swings) == len(second.confirmed_swings)
        assert len(first.pool_updates) == len(second.pool_updates)
        for p, q in zip(first.pool_updates, second.pool_updates, strict=True):
            assert p.side != q.side
            assert p.touch_count == q.touch_count
            assert p.reference_price + q.reference_price == 1000


@pytest.mark.parametrize("length", [3, 5, 7])
def test_unconfirmed_tail_is_never_a_pool(length):
    candles = tuple(bar(i, 10 + i) for i in range(length))
    assert not pool_updates(run(candles, length=length))


def test_plateau_ties_are_not_invented_equal_high_pools():
    assert not pool_updates(run(series([10, 15, 15, 10])))


def test_tolerance_band_does_not_move_when_approximate_high_is_added():
    candles = (bar(0, 10), bar(1, 15), bar(2, 12), bar(3, 12, high="16.10", low=11), bar(4, 12))
    frames = run(candles, bps="100")
    first = frames[2].pool_updates[0]
    equal = next(p for p in frames[4].pool_updates if p.side == LiquiditySide.BUY_SIDE)
    assert equal.pool_id == first.pool_id
    assert (equal.lower_bound, equal.reference_price, equal.upper_bound) == (
        Decimal("15.84"),
        Decimal(16),
        Decimal("16.16"),
    )
    assert equal.kind == LiquidityKind.EQUAL_HIGHS
    assert not frames[3].sweeps


def test_touch_exactly_at_band_edge_can_join_without_breach():
    candles = (bar(0, 10), bar(1, 15), bar(2, 12), bar(3, 12, high="16.16", low=11), bar(4, 12))
    frames = run(candles, bps="100")
    assert not frames[3].sweeps
    assert any(p.kind == LiquidityKind.EQUAL_HIGHS for p in frames[4].pool_updates)


def test_overlapping_bands_choose_oldest_match_without_transitive_merging():
    highs = [91, 100, 91, "98.5", 91, "99.2", 91]
    candles = tuple(bar(i, 90, high=h, low=89) for i, h in enumerate(highs))
    engine = analyzer(bps="100")
    for candle in candles:
        engine.update(candle)
    assert len(engine.active_pools) == 2
    old, new = engine.active_pools
    assert old.reference_price == 100 and old.touch_count == 2
    assert new.reference_price == Decimal("98.5") and new.touch_count == 1
    assert [m.swing.pivot_index for m in old.members] == [1, 5]
    assert sum(p.touch_count for p in engine.active_pools) == 3


def test_outside_candle_can_seed_both_sides_without_inferred_intrabar_order():
    frames = run((bar(0, 10), bar(1, 10, high=15, low=5), bar(2, 10)))
    assert [p.side for p in frames[2].pool_updates] == [
        LiquiditySide.BUY_SIDE,
        LiquiditySide.SELL_SIDE,
    ]


def test_retired_pools_leave_active_state_and_are_never_resurrected():
    engine = analyzer()
    outputs = tuple(engine.update(c) for c in golden())
    retired = {p.pool_id for p in pool_updates(outputs) if p.status != PoolStatus.ACTIVE}
    assert retired.isdisjoint(p.pool_id for p in engine.active_pools)
    assert [p.reference_price for p in engine.active_pools] == [17, 8]
    assert all(p.touch_count == 1 for p in engine.active_pools)


def test_geometry_is_independent_of_decimal_precision():
    candles = golden()
    expected = run(candles, bps="1.12345678")
    with localcontext() as context:
        context.prec = 3
        actual = run(candles, bps="1.12345678")
    assert actual == expected
