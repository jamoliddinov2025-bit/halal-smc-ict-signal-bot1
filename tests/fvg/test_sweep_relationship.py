from datetime import timedelta

from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer, LiquiditySide
from tests.analysis.helpers import bar
from tests.fvg.helpers import ANALYSIS, DISPLACEMENT, LIQUIDITY, SERIES, analyzer, events, run
from tests.liquidity.helpers import golden as pool_candles


def after_sell(*, doji=False):
    return (
        *pool_candles(),
        bar(12, 12 if doji else 22, opening=12, high=23, low=11),
        bar(13, 24, opening=22, high=25, low=21),
    )


def test_preceding_sell_side_sweep_is_exactly_the_middle_candles_context():
    frames = run(after_sell())
    event = frames[13].events[0]
    middle = frames[12].upstream
    assert event.preceding_sweeps is middle.preceding_sweeps
    assert [s.side for s in event.preceding_sweeps] == [LiquiditySide.SELL_SIDE]
    assert [s.breach.reference.candle_index for s in event.preceding_sweeps] == [10]
    assert event.preceding_sweep_references == tuple(
        s.provenance.as_reference() for s in middle.preceding_sweeps
    )
    assert all(ref in event.provenance.dependencies for ref in event.preceding_sweep_references)


def test_preceding_buy_side_sweep_and_bearish_gap():
    candles = (
        *pool_candles()[:10],
        bar(10, 3, opening=12, high=13, low=2),
        bar(11, 2, opening=3, high=4, low=1),
    )
    event = run(candles)[11].events[0]
    assert event.direction.value == "bearish"
    assert [(s.side.value, s.breach.reference.candle_index) for s in event.preceding_sweeps] == [
        ("buy_side", 9)
    ]


def test_context_is_available_even_when_middle_did_not_displace():
    event = run(after_sell(doji=True))[13].events[0]
    assert event.associated_displacement is None
    assert event.preceding_sweeps and event.preceding_sweeps[0].side == LiquiditySide.SELL_SIDE


def test_middle_same_candle_sweep_is_not_reselected_at_c3():
    candles = (
        *pool_candles()[:9],
        bar(9, 2, opening=12, high=17, low=1),
        bar(10, 2, opening=2, high=3, low=1),
    )
    frames = run(candles)
    assert frames[9].upstream.liquidity.sweeps
    assert frames[10].upstream.preceding_sweeps  # eligible for C3, not retroactive context for C2
    event = frames[10].events[0]
    assert event.preceding_sweeps == ()
    assert all(
        ref.evidence_id != frames[9].upstream.liquidity.sweeps[0].sweep_id
        for ref in event.provenance.dependencies
    )


def test_detection_candle_sweep_is_not_attached_instead_of_middle_context():
    candles = (*pool_candles(), bar(12, 15, opening=15, high=18, low=14))
    frames = run(candles)
    current = frames[12].upstream
    assert current.liquidity.sweeps[0].side == LiquiditySide.BUY_SIDE
    event = frames[12].events[0]
    assert [s.side for s in event.preceding_sweeps] == [LiquiditySide.SELL_SIDE]
    assert (
        current.liquidity.sweeps[0].provenance.as_reference()
        not in event.preceding_sweep_references
    )


def test_both_sides_in_original_middle_context_are_preserved_without_selection():
    candles = (
        *pool_candles()[:4],
        bar(4, 12, opening=12, high=17, low=10),
        bar(5, 26, opening=12, high=27, low=11),
        bar(6, 29, opening=28, high=30, low=28),
    )
    event = run(candles)[6].events[0]
    assert len(event.preceding_sweeps) == 2
    assert {s.side for s in event.preceding_sweeps} == {
        LiquiditySide.BUY_SIDE,
        LiquiditySide.SELL_SIDE,
    }
    assert all(s.breach.reference.candle_index == 4 for s in event.preceding_sweeps)


def test_sweep_arriving_after_c2_open_is_not_backfilled_even_if_known_by_c3():
    candles = (
        *pool_candles()[:9],
        bar(9, 2, opening=12, high=17, low=1),
        bar(10, 31, opening=2, high=32, low=1),
        bar(11, 31, opening=31, high=33, low=30),
    )
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    detector = analyzer()
    frames = []
    for index, candle in enumerate(candles):
        known = candles[10].timestamp + timedelta(microseconds=1) if index == 9 else None
        frames.append(
            detector.update(displacement.update(source.update(candle, available_at=known)))
        )
    assert frames[9].upstream.liquidity.sweeps
    assert frames[11].upstream.preceding_sweeps
    assert frames[11].events[0].preceding_sweeps == ()


def test_future_sweep_cannot_change_old_formation_context():
    candles = after_sell()
    previous = run(candles)
    extended = run(
        (
            *candles,
            bar(14, 22, opening=24, high=27, low=20),
            bar(15, 26, opening=22, high=28, low=19),
        )
    )
    assert extended[: len(candles)] == previous
    assert events(extended[: len(candles)]) == events(previous)
