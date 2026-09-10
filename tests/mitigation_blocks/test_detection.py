from dataclasses import replace

import pytest

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.mitigation_blocks import MitigationDirection
from smcsignal.analysis.order_blocks import StructureRequirement
from tests.analysis.helpers import bar, mirrored
from tests.breaker_blocks.helpers import multiple
from tests.mitigation_blocks.helpers import (
    DISPLACEMENT_ONLY,
    OB,
    before_breaker,
    bullish_mitigation,
    events,
    golden,
    nested,
    post_breaker,
    repeated,
    run,
)
from tests.order_blocks.helpers import bullish, choch, simple


@pytest.mark.parametrize("mirror", [False, True])
def test_bullish_ob_produces_bullish_mitigation_and_bearish_ob_produces_bearish(mirror):
    candles = mirrored(bullish_mitigation()) if mirror else bullish_mitigation()
    frames = run(candles)
    (event,) = events(frames)
    assert event.direction == (
        MitigationDirection.BEARISH if mirror else MitigationDirection.BULLISH
    )
    origin = frames[6].upstream.upstream.upstream.upstream.events[0]
    assert event.evidence.original_ob is origin
    assert event.original_ob_id == origin.event_id
    assert event.original_candidate_index == 4 and event.original_ob_confirmation_index == 6
    assert event.interaction_index == event.confirmation_index == 7
    assert event.original_lower_boundary == origin.zone_lower_boundary
    assert event.original_upper_boundary == origin.zone_upper_boundary
    assert event.zone_size == origin.zone_size
    assert (
        event.original_candidate_timestamp
        < event.original_ob_confirmation_timestamp
        < event.interaction_timestamp
        < event.available_at
    )
    assert event.original_ob_available_at <= event.evidence.interaction_candle.reference.opened_at
    assert origin.direction == (TrendDirection.BEARISH if mirror else TrendDirection.BULLISH)


def test_two_directions_with_default_confirmation_thresholds():
    found = events(run(golden(), displacement=DisplacementConfig()))
    assert [
        (e.original_ob_confirmation_index, e.confirmation_index, e.direction.value) for e in found
    ] == [(20, 21, "bullish"), (23, 24, "bearish")]


def test_first_post_publication_interaction_is_emitted_once():
    frames = run(repeated())
    found = events(frames)
    assert [e.confirmation_index for e in found] == [7]
    assert not frames[8].events
    assert frames[7].events[0].original_ob_id == found[0].original_ob_id


def test_repeated_later_overlaps_do_not_emit_another_mitigation():
    candles = (*repeated(), bar(9, 16, opening=16, high=17, low=14))
    frames = run(candles)
    assert len(events(frames)) == 1
    assert events(frames)[0].confirmation_index == 7


def test_pre_publication_overlap_is_not_a_mitigation():
    assert not events(run(bullish()[:7]))
    frames = run(bullish())
    origin = frames[6].upstream.upstream.upstream.upstream.events[0]
    assert origin.candidate.candle.low <= origin.zone_upper_boundary
    assert not events(frames)


def test_delayed_fvg_publication_ignores_overlap_before_the_ob_exists():
    config = replace(OB, require_fvg=True)
    candles = (
        *bullish()[:7],
        bar(7, 22, opening=20, high=23, low=19),
        bar(8, 16, opening=20, high=21, low=14),
    )
    frames = run(candles, order_blocks=config)
    assert frames[6].upstream.upstream.upstream.upstream.events == ()
    assert frames[7].upstream.upstream.upstream.upstream.events
    assert not frames[7].events
    (event,) = events(frames)
    assert event.original_ob_confirmation_index == 7
    assert event.confirmation_index == 8


def test_identical_independent_obs_emit_in_source_publication_order():
    frames = run(multiple(), displacement=DisplacementConfig(), order_blocks=DISPLACEMENT_ONLY)
    group = frames[18].events
    assert [e.original_ob_confirmation_index for e in group] == [15, 16]
    assert [(e.original_lower_boundary, e.original_upper_boundary) for e in group] == [
        (70, 75),
        (70, 75),
    ]
    assert group[0].direction == group[1].direction == MitigationDirection.BULLISH
    assert group[0].original_ob_id != group[1].original_ob_id
    assert [e.event_id for e in group] == [e.provenance.evidence_id for e in group]


def test_nested_obs_remain_independent_and_are_not_merged():
    frames = run(nested(), order_blocks=DISPLACEMENT_ONLY)
    found = events(frames)
    assert [
        (e.original_ob_confirmation_index, e.original_lower_boundary, e.original_upper_boundary)
        for e in found
    ] == [
        (6, 13, 15),
        (9, 12, 18),
    ]
    assert found[0].original_ob_id != found[1].original_ob_id
    assert found[0].event_id != found[1].event_id


def test_mitigation_before_breaker_remains_and_conversion_still_occurs():
    frames = run(before_breaker())
    (event,) = events(frames)
    assert event.confirmation_index == 7
    assert frames[9].upstream.events
    assert frames[9].upstream.events[0].original_ob_id == event.original_ob_id
    assert event.original_ob_id not in {e.original_ob_id for e in events(frames[9:])}


def test_post_breaker_return_does_not_create_a_first_mitigation():
    frames = run(post_breaker())
    assert frames[8].upstream.events
    assert frames[8].upstream.events[0].original_ob_confirmation_index == 6
    assert not events(frames)
    candle = frames[9].upstream.upstream.upstream.observation.candle
    origin = frames[6].upstream.upstream.upstream.upstream.events[0]
    assert candle.high > origin.zone_lower_boundary and candle.low < origin.zone_upper_boundary


def test_immutable_prior_mitigation_is_not_rewritten_by_later_breaker():
    frames = run(before_breaker())
    first = frames[7].events[0]
    saved = (
        first.event_id,
        first.original_ob_id,
        first.overlap_lower,
        first.overlap_upper,
        first.available_at,
    )
    later = frames[9].upstream.events[0]
    assert later.original_ob_id == first.original_ob_id
    assert (
        frames[7].events[0].event_id,
        frames[7].events[0].original_ob_id,
        frames[7].events[0].overlap_lower,
        frames[7].events[0].overlap_upper,
        frames[7].events[0].available_at,
    ) == saved


def test_no_retroactive_upgrade_of_a_missed_or_rejected_interaction():
    candles = (
        *bullish()[:7],
        bar(7, 20, opening=20, high=21, low=19),
        bar(8, 16, opening=20, high=21, low=14),
    )
    frames = run(candles)
    assert not frames[7].events
    (event,) = events(frames)
    assert event.confirmation_index == 8
    assert event.original_ob_confirmation_index == 6


@pytest.mark.parametrize("count", [0, 1, 2, 3, 6, 7])
def test_insufficient_history_or_unreturned_ob_produces_no_mitigation(count):
    assert not events(run(bullish()[:count]))


def test_new_ob_cannot_mitigate_on_its_own_publication_candle():
    frames = run(choch())
    assert frames[6].upstream.upstream.upstream.upstream.events
    assert not frames[6].events
    assert frames[8].upstream.upstream.upstream.upstream.events
    assert not frames[8].events


def test_displacement_only_source_still_mitigates_without_rerunning_detectors():
    candles = (*simple(), bar(5, 100, opening=105, high=106, low=100))
    frames = run(candles, order_blocks=DISPLACEMENT_ONLY)
    (event,) = events(frames)
    assert event.evidence.original_ob.structure_event is None
    assert (
        event.evidence.original_ob.settings.structure_requirement
        == StructureRequirement.DISPLACEMENT_ONLY
    )
    assert event.confirmation_index == 5
