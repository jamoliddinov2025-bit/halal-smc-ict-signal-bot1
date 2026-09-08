from dataclasses import replace

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.liquidity import LiquiditySide
from smcsignal.analysis.order_blocks import CandidateSelection, StructureRequirement
from tests.analysis.helpers import bar
from tests.liquidity.helpers import golden as pool_candles
from tests.order_blocks.helpers import choch, run

MODE = StructureRequirement.DISPLACEMENT_ONLY


def after_sell():
    before = list(pool_candles())
    before[11] = bar(11, 12, opening=13, high=13, low=10)
    return (*before, bar(12, 22, opening=12, high=23, low=11))


def test_actual_preceding_sell_sweep_group_is_reused():
    frames = run(after_sell(), structure_requirement=MODE)
    event = frames[12].events[0]
    assert event.candidate_index == 11
    assert event.preceding_sweeps is event.displacement.preceding_sweeps
    assert event.preceding_sweep_references is event.displacement.preceding_sweep_references
    assert [(s.side, s.breach.reference.candle_index) for s in event.preceding_sweeps] == [
        (LiquiditySide.SELL_SIDE, 10)
    ]
    assert all(r in event.provenance.dependencies for r in event.preceding_sweep_references)


def test_actual_preceding_buy_sweep_is_reused():
    before = list(pool_candles()[:10])
    before[9] = bar(9, 12, opening=11, high=17, low=11)
    frames = run((*before, bar(10, 3, opening=12, high=13, low=2)), structure_requirement=MODE)
    event = frames[10].events[0]
    assert event.direction == TrendDirection.BEARISH and event.candidate_index == 9
    assert [s.side for s in event.preceding_sweeps] == [LiquiditySide.BUY_SIDE]


def test_same_displacement_candle_sweep_is_not_reinterpreted_as_preceding():
    before = list(pool_candles()[:9])
    before[8] = bar(8, 12, opening=11, high=13, low=11)
    frames = run((*before, bar(9, 2, opening=12, high=17, low=1)), structure_requirement=MODE)
    event = frames[9].events[0]
    assert frames[9].upstream.upstream.liquidity.sweeps
    assert event.preceding_sweeps == () and event.preceding_sweep_references == ()


def test_all_members_of_both_side_sweep_cohort_are_retained():
    candles = (
        *pool_candles()[:4],
        bar(4, 12, opening=12, high=17, low=10),
        bar(5, 12, opening=13, high=13, low=11),
        bar(6, 26, opening=12, high=27, low=11),
    )
    event = run(candles, structure_requirement=MODE)[6].events[0]
    assert {s.side for s in event.preceding_sweeps} == {
        LiquiditySide.BUY_SIDE,
        LiquiditySide.SELL_SIDE,
    }
    assert len(event.preceding_sweeps) == 2


def test_delayed_ob_uses_displacement_sweeps_not_new_publication_context():
    candles = (*after_sell(), bar(13, 24, opening=22, high=25, low=21))
    event = run(candles, structure_requirement=MODE, require_fvg=True)[13].events[0]
    assert event.candidate_index == 11 and event.displacement_index == 12
    assert event.preceding_sweeps is event.displacement.preceding_sweeps
    assert all(s.breach.reference.candle_index == 10 for s in event.preceding_sweeps)


def test_future_sweeps_do_not_modify_already_observable_ob():
    candles = after_sell()
    prefix = run(candles, structure_requirement=MODE)
    extended = run((*candles, bar(13, 12, opening=22, high=24, low=10)), structure_requirement=MODE)
    assert prefix == extended[: len(candles)]


def test_opposing_structure_does_not_satisfy_or_masquerade_as_matching_structure():
    candles = list(choch())
    candles[0] = replace(candles[0], open=candles[0].high)
    candles[8] = bar(8, 12, opening=2, high=13, low=2)
    default = run(candles, candidate_selection=CandidateSelection.EARLIEST)
    current = default[8].upstream.upstream
    assert current.events[0].direction == TrendDirection.BULLISH
    assert current.liquidity.context.snapshot.events[0].direction == TrendDirection.BEARISH
    assert not default[8].events
    relaxed = run(
        candles, candidate_selection=CandidateSelection.EARLIEST, structure_requirement=MODE
    )
    event = relaxed[8].events[0]
    assert event.candidate_index == 0 and event.structure_event is None
