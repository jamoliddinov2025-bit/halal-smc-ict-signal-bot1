from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from smcsignal.analysis import StructureEventKind, TrendDirection
from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.order_blocks import CandleClassification, StructureRequirement, ZoneBasis
from tests.analysis.helpers import bar, mirrored
from tests.order_blocks.helpers import bullish, choch, events, golden, run, simple, upstream


@pytest.mark.parametrize("mirror", [False, True])
def test_default_rule_requires_actual_matching_displacement_and_bos(mirror):
    candles = mirrored(bullish()) if mirror else bullish()
    frames = run(candles)
    (event,) = events(frames)
    assert event.candidate_index == 4 and event.displacement_index == event.confirmation_index == 6
    assert event.direction == (TrendDirection.BEARISH if mirror else TrendDirection.BULLISH)
    assert event.candidate_classification == (
        CandleClassification.BULLISH if mirror else CandleClassification.BEARISH
    )
    assert event.structure_event.kind == StructureEventKind.BOS
    assert event.structure_event.direction == event.displacement.direction == event.direction
    assert event.structure_event is event.displacement_frame.liquidity.context.snapshot.events[0]
    assert event.structure_reference == event.structure_context.provenance.as_reference()
    assert event.displacement is frames[6].upstream.upstream.events[0]
    assert event.zone_lower_boundary == (985 if mirror else 13)
    assert event.zone_upper_boundary == (987 if mirror else 15)
    assert event.zone_size == 2
    assert not frames[4].events and not frames[5].events
    assert event.candidate_timestamp < event.confirmation_timestamp < event.available_at


@pytest.mark.parametrize("mirror", [False, True])
def test_choch_confirmation_is_linked_on_the_displacement_candle(mirror):
    candles = mirrored(choch()) if mirror else choch()
    found = events(run(candles, structure_requirement=StructureRequirement.CHOCH))
    (event,) = found
    assert event.candidate_index == 7 and event.confirmation_index == 8
    assert event.structure_event.kind == StructureEventKind.CHOCH
    assert event.structure_confirmation_index == event.displacement_index == 8
    assert event.structure_available_at == event.displacement_available_at == event.available_at


def test_bos_only_does_not_accept_choch_and_choch_only_does_not_accept_bos():
    assert [
        e.confirmation_index
        for e in events(run(choch(), structure_requirement=StructureRequirement.BOS))
    ] == [6]
    assert not events(run(bullish(), structure_requirement=StructureRequirement.CHOCH))


def test_no_structure_default_rejects_but_displacement_only_mode_is_explicit():
    candles = simple()
    assert upstream(candles)[4].upstream.events
    assert not upstream(candles)[4].upstream.liquidity.context.snapshot.events
    assert not events(run(candles))
    (event,) = events(run(candles, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY))
    assert event.candidate_index == 3
    assert event.structure_event is None and event.structure_reference is None
    assert event.structure_confirmation_timestamp is None


def test_optional_structure_is_preserved_when_available_in_displacement_only_mode():
    (event,) = events(run(bullish(), structure_requirement=StructureRequirement.DISPLACEMENT_ONLY))
    assert event.structure_event.kind == StructureEventKind.BOS


def test_ob_without_displacement_is_impossible_in_all_modes():
    candles = simple(confirmation=bar(4, 101, opening=100, high=102, low=99))
    assert not upstream(candles)[4].upstream.events
    for mode in StructureRequirement:
        assert not events(run(candles, structure_requirement=mode))


def test_default_example_has_bullish_bos_and_bearish_choch_formations():
    found = events(run(golden(), displacement=DisplacementConfig()))
    assert [
        (
            e.candidate_index,
            e.displacement_index,
            e.confirmation_index,
            e.direction.value,
            e.zone_lower_boundary,
            e.zone_upper_boundary,
            e.structure_event.kind.value,
        )
        for e in found
    ] == [
        (18, 20, 20, "bullish", Decimal(13), Decimal(15), "BOS"),
        (21, 22, 22, "bearish", Decimal(19), Decimal(23), "CHoCH"),
    ]


@pytest.mark.parametrize("mirror", [False, True])
def test_zone_departure_is_strict_not_equal(mirror):
    candles = list(simple())
    candles[3] = bar(3, 100, opening=101, high=106, low=99)
    candles[4] = bar(4, 106, opening=100, high=108, low=100)
    if mirror:
        candles = mirrored(candles)
    assert upstream(candles)[4].upstream.events
    assert not events(run(candles, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY))
    (body_event,) = events(
        run(
            candles,
            structure_requirement=StructureRequirement.DISPLACEMENT_ONLY,
            zone_basis=ZoneBasis.BODY,
        )
    )
    assert body_event.zone_size == 1


def test_exactly_above_zone_boundary_qualifies_without_epsilon_rounding():
    candles = list(simple())
    candles[3] = bar(3, 100, opening=101, high=106, low=99)
    candles[4] = bar(4, "106.0000000000000000000000000001", opening=100, high=108, low=100)
    expected = run(candles, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY)
    assert events(expected)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        assert (
            run(candles, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY) == expected
        )


def test_gapped_displacement_with_real_body_can_confirm_without_invented_path():
    candles = simple(confirmation=bar(4, 125, opening=120, high=126, low=119))
    (event,) = events(run(candles, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY))
    assert event.displacement.observation.candle.open > event.zone_upper_boundary
    assert event.displacement.body_size == 5


def test_gap_only_small_body_does_not_create_displacement_or_ob():
    assert not events(
        run(
            simple(confirmation=bar(4, 121, opening=120, high=122, low=119)),
            structure_requirement=StructureRequirement.DISPLACEMENT_ONLY,
        )
    )


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_insufficient_history_does_not_publish_candidates_as_order_blocks(count):
    assert not events(run(bullish()[:count]))
