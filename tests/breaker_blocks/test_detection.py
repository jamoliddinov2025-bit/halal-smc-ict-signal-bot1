import pytest

from smcsignal.analysis import TrendDirection
from smcsignal.analysis.breaker_blocks import BreakerDirection, BreakerRejection
from smcsignal.analysis.displacement import DisplacementConfig
from tests.analysis.helpers import bar, mirrored
from tests.breaker_blocks.helpers import (
    DISPLACEMENT_ONLY,
    base,
    evaluations,
    events,
    golden,
    multiple,
    run,
)
from tests.order_blocks.helpers import choch, simple


@pytest.mark.parametrize("mirror", [False, True])
def test_strict_opposing_mss_displacement_confirms_exact_preexisting_ob(mirror):
    candles = mirrored(choch()) if mirror else choch()
    frames = run(candles)
    (event,) = events(frames)
    assert event.direction == (BreakerDirection.BULLISH if mirror else BreakerDirection.BEARISH)
    origin = frames[6].upstream.upstream.upstream.events[0]
    assert event.evidence.original_ob is origin
    assert event.original_ob_id == origin.event_id
    assert event.original_candidate_index == 4 and event.original_ob_confirmation_index == 6
    assert event.invalidation_index == event.confirmation_index == event.mss_confirmation_index == 8
    assert event.lower_boundary == event.original_lower_boundary == origin.zone_lower_boundary
    assert event.upper_boundary == event.original_upper_boundary == origin.zone_upper_boundary
    assert event.zone_size == origin.zone_size
    assert (
        event.original_candidate_timestamp
        < event.original_ob_confirmation_timestamp
        < event.invalidation_timestamp
        < event.available_at
    )
    assert event.original_ob_available_at <= event.evidence.invalidating_candle.reference.opened_at
    assert event.evidence.displacement is frames[8].upstream.events[0].evidence.displacement
    assert event.evidence.mss is frames[8].upstream.events[0]


def test_two_directions_with_default_confirmation_thresholds():
    found = events(run(golden(), displacement=DisplacementConfig()))
    assert [
        (e.original_ob_confirmation_index, e.confirmation_index, e.direction.value) for e in found
    ] == [(20, 22, "bearish"), (22, 27, "bullish")]


def test_consecutive_multiple_identical_and_nested_zones_are_all_retained():
    frames = run(multiple(), displacement=DisplacementConfig(), order_blocks=DISPLACEMENT_ONLY)
    found = events(frames)
    assert [(e.confirmation_index, e.direction.value) for e in found] == [
        (21, "bullish"),
        (22, "bearish"),
        (22, "bearish"),
        (22, "bearish"),
    ]
    group = frames[22].events
    assert [e.original_ob_confirmation_index for e in group] == [15, 16, 21]
    assert [(e.lower_boundary, e.upper_boundary) for e in group] == [(70, 75), (70, 75), (70, 90)]
    assert len({e.original_ob_id for e in group}) == 3
    assert len({e.event_id for e in group}) == 3


def test_overlapping_sources_are_not_merged_or_reduced_to_nearest():
    frames = run(
        multiple(overlap=True), displacement=DisplacementConfig(), order_blocks=DISPLACEMENT_ONLY
    )
    assert [
        (e.original_ob_confirmation_index, e.lower_boundary, e.upper_boundary)
        for e in frames[22].events
    ] == [(15, 70, 85), (16, 70, 85), (21, 80, 100)]
    # The nearest eligible zone is included, but so are the independent older ones.
    assert frames[22].events[-1].original_ob_confirmation_index == 21


def test_opposing_new_ob_on_break_candle_is_not_converted_on_its_own_publication():
    frames = run(choch())
    assert frames[8].upstream.upstream.upstream.events[0].direction == TrendDirection.BEARISH
    assert all(e.original_ob_confirmation_index < e.confirmation_index for e in events(frames))


@pytest.mark.parametrize("count", [0, 1, 2, 3, 6, 7])
def test_insufficient_history_no_source_ob_or_unbroken_ob_produces_no_breaker(count):
    assert not events(run(base()[:count]))


def test_missing_displacement_is_a_recorded_rejection_not_a_breaker():
    candles = (*base()[:7], bar(7, 10, opening=10, high=11, low=9))
    frame = run(candles)[7]
    assert not frame.events
    assert frame.evidence[0].rejection_reason == BreakerRejection.MISSING_DISPLACEMENT
    assert not frame.evidence[0].qualified


def test_real_displacement_without_mss_is_not_enough():
    candles = (*simple(), bar(5, 81, opening=105, high=106, low=80))
    frames = run(candles, order_blocks=DISPLACEMENT_ONLY)
    assert frames[4].upstream.upstream.upstream.events
    assert not frames[5].upstream.events
    assert frames[5].evidence[0].displacement is not None
    assert frames[5].evidence[0].rejection_reason == BreakerRejection.MISSING_MSS
    assert not frames[5].events


def test_wrong_direction_body_does_not_confirm_opposing_ob_violation():
    candles = (*simple(), bar(5, 95, opening=90, high=96, low=80))
    frame = run(candles, order_blocks=DISPLACEMENT_ONLY)[5]
    assert frame.evidence[0].displacement.direction == TrendDirection.BULLISH
    assert frame.evidence[0].direction == BreakerDirection.BEARISH
    assert frame.evidence[0].rejection_reason == BreakerRejection.WRONG_DISPLACEMENT_DIRECTION
    assert not frame.events


def test_failed_first_violation_is_not_upgraded_by_later_matching_mss():
    frames = run(
        multiple(first_failure=True),
        displacement=DisplacementConfig(),
        order_blocks=DISPLACEMENT_ONLY,
    )
    rejected = frames[18].evidence
    assert [e.original_ob.confirmation_index for e in rejected] == [15, 16]
    assert all(e.rejection_reason == BreakerRejection.MISSING_MSS for e in rejected)
    assert frames[22].upstream.events  # A later real MSS does exist.
    old_ids = {e.original_ob_id for e in rejected}
    assert old_ids.isdisjoint(e.original_ob_id for e in events(frames))
    assert sum(e.original_ob_id in old_ids for e in evaluations(frames)) == 2


def test_repeated_strict_closes_cannot_repeat_one_source_conversion():
    candles = (*choch(), bar(9, 7, opening=9, high=10, low=6), bar(10, 5, opening=7, high=8, low=4))
    frames = run(candles)
    first = frames[8].events[0]
    assert sum(e.original_ob_id == first.original_ob_id for e in evaluations(frames)) == 1
    assert sum(e.original_ob_id == first.original_ob_id for e in events(frames)) == 1


def test_ob_first_published_already_beyond_far_boundary_is_not_backdated():
    candles = tuple(bar(i, 100, opening=100, high=101, low=99) for i in range(4))
    candles = (
        *candles,
        bar(4, 101, opening=110, high=111, low=100),
        bar(5, 50, opening=50, high=51, low=49),
        bar(6, 120, opening=50, high=121, low=50),
        bar(7, 70, opening=70, high=71, low=60),
        bar(8, 65, opening=65, high=66, low=64),
    )
    from dataclasses import replace

    config = replace(DISPLACEMENT_ONLY, require_fvg=True)
    frames = run(candles, order_blocks=config)
    assert frames[7].upstream.upstream.upstream.events
    born = frames[7].evidence[0]
    assert born.original_ob.confirmation_index == 7
    assert born.rejection_reason == BreakerRejection.SOURCE_NOT_KNOWN_AT_OPEN
    assert not frames[7].events
    assert all(e.original_ob_id != born.original_ob_id for e in frames[8].evidence)


def test_mss_without_any_preexisting_source_ob_does_not_create_a_breaker():
    from tests.mss.helpers import consecutive

    frames = run(consecutive(), displacement=DisplacementConfig())
    assert frames[21].upstream.events and frames[22].upstream.events
    assert not events(frames)
