from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from smcsignal.analysis.order_blocks import (
    CandidateSelection,
    CandleClassification,
    StructureRequirement,
    ZoneBasis,
)
from tests.analysis.helpers import bar, mirrored
from tests.order_blocks.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    events,
    run,
    simple,
)

MODE = StructureRequirement.DISPLACEMENT_ONLY


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize(
    "selection,index", [(CandidateSelection.NEAREST, 3), (CandidateSelection.EARLIEST, 1)]
)
def test_multiple_and_consecutive_opposite_candidates_select_one(mirror, selection, index):
    candles = simple(candidates=(1, 2, 3))
    if mirror:
        candles = mirrored(candles)
    (event,) = events(run(candles, candidate_selection=selection, structure_requirement=MODE))
    assert event.candidate_index == index
    assert event.candidate_distance == 4 - index
    assert len(event.candidate_window) == 4


@pytest.mark.parametrize("lookback,expected", [(1, False), (2, False), (3, True), (4, True)])
def test_exact_inclusive_lookback_boundary(lookback, expected):
    found = events(
        run(simple(candidates=(1,)), max_candidate_lookback=lookback, structure_requirement=MODE)
    )
    assert bool(found) == expected
    if found:
        assert found[0].candidate_index == 1 and found[0].candidate_distance == 3


def test_immediately_preceding_candidate_with_lookback_one():
    (event,) = events(run(simple(), max_candidate_lookback=1, structure_requirement=MODE))
    assert event.candidate_index == 3


def test_no_candidate_and_same_direction_candidate_do_not_emit():
    assert not events(run(simple(candidates=()), structure_requirement=MODE))
    candles = tuple(
        replace(c, open=Decimal(99)) if i < 4 else c for i, c in enumerate(simple(candidates=()))
    )
    assert not events(run(candles, structure_requirement=MODE))


def test_opposing_displacement_does_not_confirm_the_wrong_candidate_direction():
    candles = simple(confirmation=bar(4, 95, opening=100, high=101, low=94))
    assert not events(
        run(candles, structure_requirement=MODE)
    )  # preceding candidate is bearish too


def test_nearest_failed_departure_does_not_trigger_search_for_favorable_older_zone():
    candles = list(simple(candidates=(1, 3)))
    candles[3] = bar(3, 100, opening=101, high=106, low=99)
    candles[4] = bar(4, 106, opening=100, high=108, low=100)
    assert not events(run(candles, structure_requirement=MODE))
    (earliest,) = events(
        run(candles, structure_requirement=MODE, candidate_selection=CandidateSelection.EARLIEST)
    )
    assert earliest.candidate_index == 1


def test_doji_rejected_by_default_and_kept_neutral_with_explicit_override():
    candles = simple(candidates=())
    assert not events(run(candles, structure_requirement=MODE))
    (event,) = events(run(candles, structure_requirement=MODE, allow_doji=True))
    assert event.candidate_classification == CandleClassification.DOJI
    assert event.candidate.candle.open == event.candidate.candle.close
    assert event.zone_size == 2
    assert not events(
        run(candles, structure_requirement=MODE, allow_doji=True, zone_basis=ZoneBasis.BODY)
    )


def test_point_doji_is_not_a_nonzero_zone_even_with_override():
    candles = list(simple(candidates=()))
    candles[3] = bar(3, 100, opening=100, high=100, low=100)
    assert not events(
        run(candles, structure_requirement=MODE, allow_doji=True, max_candidate_lookback=1)
    )


def test_tiny_strict_body_remains_directional_without_floating_epsilon():
    candles = list(simple())
    candles[3] = bar(3, 100, opening="100.0000000000000000000000000001", high=101, low=99)
    (event,) = events(run(candles, structure_requirement=MODE, zone_basis=ZoneBasis.BODY))
    assert event.candidate_classification == CandleClassification.BEARISH
    assert event.zone_size == Decimal("1e-28")


def test_late_candidate_not_known_before_displacement_open_is_ineligible():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    detector = analyzer(structure_requirement=MODE)
    candles = simple()
    out = []
    for i, c in enumerate(candles):
        delay = candles[4].timestamp + timedelta(microseconds=1) if i == 3 else None
        out.append(
            detector.update(
                fvg.update(displacement.update(liquidity.update(c, available_at=delay)))
            )
        )
    assert out[4].upstream.upstream.events
    assert not out[4].events


def test_candidate_choice_does_not_use_future_high_low_or_zone_touches():
    candles = simple()
    original = run(candles, structure_requirement=MODE)
    extended = run(
        (*candles, bar(5, 80, opening=105, high=110, low=70)), structure_requirement=MODE
    )
    assert extended[:5] == original
    assert original[4].events[0].candidate.candle == candles[3]
