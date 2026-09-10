import pytest

from smcsignal.analysis.order_blocks import StructureRequirement
from tests.analysis.helpers import bar, mirrored
from tests.order_blocks.helpers import bullish, events, run


@pytest.mark.parametrize("mirror", [False, True])
def test_optional_fvg_confirmation_delays_publication_not_candidate_selection(mirror):
    candles = mirrored(bullish()) if mirror else bullish()
    frames = run(candles, require_fvg=True, max_candidate_lookback=2)
    assert not frames[6].events
    (event,) = frames[7].events
    assert event.candidate_index == 4  # distance 2 from displacement, not 3 from publication
    assert event.displacement_index == 6 and event.confirmation_index == 7
    assert event.structure_confirmation_index == 6 and event.fvg_confirmation_index == 7
    assert event.candidate_timestamp < event.displacement_timestamp < event.confirmation_timestamp
    assert event.candidate_available_at <= event.displacement.observation.reference.opened_at
    assert event.displacement_available_at <= event.structure_available_at < event.available_at
    assert event.fvg_available_at == event.available_at
    assert event.associated_fvg is frames[7].upstream.events[0]
    assert event.associated_fvg.associated_displacement is event.displacement
    assert event.fvg_reference == event.associated_fvg.provenance.as_reference()
    assert event.fvg_reference in event.provenance.dependencies


def test_default_publication_never_acquires_future_fvg_or_changes_id():
    prefix = run(bullish()[:7])
    full = run(bullish())
    assert prefix == full[:7]
    (event,) = prefix[6].events
    assert event.associated_fvg is None and event.fvg_reference is None
    assert full[7].upstream.events
    assert full[6].events[0] is not full[7].upstream.events[0]
    assert prefix[6].events[0].event_id == full[6].events[0].event_id


def test_required_fvg_absence_expires_without_tail_flush_or_later_backfill():
    candles = (
        *bullish()[:7],
        bar(7, 21, opening=20, high=23, low=16),
        bar(8, 24, opening=23, high=25, low=23),
    )
    assert run(candles[:7], require_fvg=True)[6].events == ()
    assert not events(run(candles, require_fvg=True))


def test_opposing_fvg_of_same_displacement_is_not_confirmation():
    candles = (*bullish()[:7], bar(7, 9, opening=10, high=12, low=8))
    frames = run(candles, require_fvg=True)
    assert frames[7].upstream.events
    gap = frames[7].upstream.events[0]
    assert gap.associated_displacement.direction != gap.direction
    assert not events(frames)


def test_future_structure_on_c3_cannot_rescue_missing_structure_at_displacement():
    candles = (
        *bullish()[:6],
        bar(6, "18.5", opening=12, high="19.5", low=12),
        bar(7, 22, opening="18.5", high=23, low=18),
    )
    default = run(candles, require_fvg=True)
    assert default[6].upstream.upstream.events
    assert not default[6].upstream.upstream.liquidity.context.snapshot.events
    assert (
        default[7].upstream.events
        and default[7].upstream.upstream.liquidity.context.snapshot.events
    )
    assert not events(default)
    relaxed = run(
        candles, require_fvg=True, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY
    )
    (event,) = relaxed[7].events
    assert event.structure_event is None and event.structure_reference is None


def test_a_new_opposite_candle_at_publication_is_not_selected_retroactively():
    candles = (*bullish()[:7], bar(7, 20, opening=21, high=23, low=19))
    frames = run(candles, require_fvg=True)
    assert frames[7].upstream.upstream.metrics.direction.value == "bearish"
    assert frames[7].events[0].candidate_index == 4
    assert frames[7].events[0].candidate_window[-1].metrics.observation.reference.candle_index == 5


def test_unrelated_current_fvg_is_not_attached_in_default_mode():
    from tests.order_blocks.helpers import simple

    candles = simple(confirmation=bar(4, 108, opening=102, high=109, low=102))
    frame = run(candles, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY)[4]
    assert frame.upstream.events and frame.events
    assert frame.upstream.events[0].associated_displacement is None
    assert frame.events[0].associated_fvg is None


def test_required_mode_full_prefix_identity_at_publication_boundary():
    candles = bullish()
    full = run(candles, require_fvg=True)
    for cut in range(len(candles) + 1):
        assert run(candles[:cut], require_fvg=True) == full[:cut]


def test_consecutive_displacements_can_confirm_prior_wait_and_start_a_new_one():
    from smcsignal.analysis.displacement import DisplacementConfig
    from tests.order_blocks.helpers import simple

    candles = (
        *simple(),
        bar(5, 116, opening=105, high=117, low=104),
        bar(6, "117.5", opening=117, high=118, low=117),
    )
    frames = run(
        candles,
        displacement=DisplacementConfig(atr_period=1),
        structure_requirement=StructureRequirement.DISPLACEMENT_ONLY,
        require_fvg=True,
    )
    assert [e.confirmation_index for e in events(frames)] == [5, 6]
    assert [e.displacement_index for e in events(frames)] == [4, 5]
    assert [e.candidate_index for e in events(frames)] == [3, 3]
    assert frames[5].events[0].event_id != frames[6].events[0].event_id
