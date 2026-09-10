from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis import StructureEventKind, TrendDirection
from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.mss import MSSDirection
from tests.analysis.helpers import bar, mirrored
from tests.displacement.helpers import bull, warmup
from tests.mss.helpers import base, consecutive, events, golden, run, upstream
from tests.order_blocks.helpers import bullish as bos_candles


def test_bearish_and_bullish_mss_reference_exact_known_control_break_and_displacement():
    frames = run(base())
    found = events(frames)
    assert [(e.detection_index, e.direction) for e in found] == [
        (8, MSSDirection.BEARISH),
        (13, MSSDirection.BULLISH),
    ]
    for e in found:
        proof = e.evidence
        assert (
            proof.displacement
            is frames[e.detection_index].upstream.upstream.upstream.upstream.events[0]
        )
        assert proof.structure_break.kind == StructureEventKind.CHOCH
        assert proof.structure_break.direction.value == e.direction.value
        assert proof.prior_control.value != e.direction.value
        assert proof.prior_structure_context.snapshot.trend.ready
        assert (
            proof.prior_structure_context.provenance.available_at
            <= proof.current.observation.reference.opened_at
        )
        assert (
            proof.broken_level.provenance.available_at
            <= proof.current.observation.reference.opened_at
        )
        assert proof.broken_level.swing == proof.structure_break.level
        assert e.available_at == proof.current.observation.available_at
        assert e.timestamp == proof.current.observation.reference.opened_at
    assert found[0].evidence.level_price == 13
    assert found[1].evidence.level_price == 15


def test_mirrored_series_reverses_mss_direction_without_changing_detection_indices():
    found = events(run(mirrored(base())))
    assert [(e.detection_index, e.direction) for e in found] == [
        (8, MSSDirection.BULLISH),
        (13, MSSDirection.BEARISH),
    ]


def test_default_upstream_thresholds_and_atr_example_has_two_shifts():
    found = events(run(golden(), displacement=DisplacementConfig()))
    assert [(e.detection_index, e.direction.value, e.evidence.level_price) for e in found] == [
        (22, "bearish", Decimal(13)),
        (27, "bullish", Decimal(15)),
    ]


def test_consecutive_mss_can_occur_when_prior_confirmed_control_really_changes():
    frames = run(consecutive(), displacement=DisplacementConfig())
    assert [(e.detection_index, e.direction.value) for e in events(frames)] == [
        (21, "bullish"),
        (22, "bearish"),
    ]
    assert frames[21].events[0].evidence.prior_control == TrendDirection.BEARISH
    assert frames[22].events[0].evidence.prior_control == TrendDirection.BULLISH
    assert frames[22].events[0].evidence.broken_level.swing.confirmed_index == 21


def test_mss_does_not_relabel_phase_three_trend_or_pd_to_force_reversal():
    frames = run(base())
    event = frames[8].events[0]
    assert event.direction == MSSDirection.BEARISH
    assert (
        event.evidence.confirmation_structure_context.snapshot.trend.direction
        == TrendDirection.BULLISH
    )
    assert event.evidence.current is frames[8].upstream


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_insufficient_structure_never_fabricates_control(count):
    assert not events(run(base()[:count]))


def test_displacement_without_known_structure_is_not_mss():
    source = upstream((*warmup(), bull()))
    assert source[4].upstream.upstream.upstream.events
    assert source[3].upstream.upstream.upstream.liquidity.context.snapshot.trend.ready is False
    assert not events(run((*warmup(), bull())))


def test_existing_choch_without_displacement_is_not_mss():
    candles = list(base()[:9])
    candles[8] = replace(candles[8], open=candles[8].close)
    source = upstream(candles)
    assert (
        source[8].upstream.upstream.upstream.liquidity.context.snapshot.events[0].kind
        == StructureEventKind.CHOCH
    )
    assert not source[8].upstream.upstream.upstream.events
    assert not events(run(candles))


def test_bullish_body_on_bearish_close_break_is_wrong_direction_not_mss():
    candles = (*base()[:8], bar(8, 12, opening=2, high=16, low=2))
    source = upstream(candles)
    assert source[8].upstream.upstream.upstream.events[0].direction == TrendDirection.BULLISH
    assert (
        source[8].upstream.upstream.upstream.liquidity.context.snapshot.events[0].direction
        == TrendDirection.BEARISH
    )
    assert not events(run(candles))


@pytest.mark.parametrize("close", [13, 14])
def test_wick_break_or_equal_close_is_not_a_structure_break(close):
    candles = (*base()[:8], bar(8, close, opening=22, high=23, low=12))
    source = upstream(candles)
    assert source[8].upstream.upstream.upstream.events[0].direction == TrendDirection.BEARISH
    assert not source[8].upstream.upstream.upstream.liquidity.context.snapshot.events
    assert not events(run(candles))


def test_continuation_bos_plus_displacement_is_not_mss():
    source = upstream(bos_candles())
    assert source[6].upstream.upstream.upstream.events
    assert (
        source[6].upstream.upstream.upstream.liquidity.context.snapshot.events[0].kind
        == StructureEventKind.BOS
    )
    assert not events(run(bos_candles()))


def test_mixed_ready_prior_structure_is_not_retroclassified_with_new_current_trend():
    candles = (*base()[:10], bar(10, 9, opening=16, high="16.5", low=7))
    frames = run(candles)
    prior = frames[9].upstream.upstream.upstream.upstream.liquidity.context.snapshot.trend
    assert prior.ready and prior.direction == TrendDirection.RANGING
    assert frames[10].upstream.upstream.upstream.upstream.events
    assert (
        frames[10].upstream.upstream.upstream.upstream.liquidity.context.snapshot.trend.direction
        == TrendDirection.BEARISH
    )
    assert not frames[10].events


def test_staying_beyond_a_consumed_level_does_not_duplicate_mss():
    frames = run((*base(), bar(14, 59, opening=30, high=60, low=29)))
    assert frames[13].events
    assert not frames[14].events
    ids = [e.evidence.broken_level_reference.evidence_id for e in events(frames)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "close,expected",
    [("12.9999999999999999999999999999", True), ("13.0000000000000000000000000001", False)],
)
def test_exact_unrounded_close_boundary_uses_existing_structure_event(close, expected):
    candles = (*base()[:8], bar(8, close, opening=22, high=23, low=12))
    assert bool(run(candles)[8].events) == expected
