from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError, OutcomeStatus, OutcomeTrackingConfig
from smcsignal.analysis.outcome_tracking.calculation import (
    classify_outcome,
    final_difference,
    final_return,
    mae_return,
    mfe_return,
)
from tests.mtf.helpers import EIGHT, bar_at
from tests.outcome_tracking.helpers import (
    FALLING_TAIL,
    candles_for,
    outcome_for,
    ratio,
    run,
    signal_frames,
    tie_tail_candles,
)

STEP = timedelta(minutes=15)


def epsilon_tail_candles(final_close: Decimal):
    """Identical prefix 0..4; the horizon-3 final candle closes at final_close."""

    bars = [bar_at(EIGHT + index * STEP, Decimal(20 + index)) for index in range(5)]
    bars.extend(
        [
            bar_at(EIGHT + 5 * STEP, Decimal(23)),
            bar_at(EIGHT + 6 * STEP, Decimal(25)),
            bar_at(EIGHT + 7 * STEP, final_close),
        ]
    )
    return tuple(bars)


def test_extreme_progression_is_hand_computed_and_running() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    versions = outcome_for(snapshots, frames[4].signal_id)
    progression = [
        (version.mfe_price, version.mfe_index, version.mae_price, version.mae_index)
        for version in versions
    ]
    assert progression == [
        (None, None, None, None),
        (Decimal(26), 5, Decimal(24), 5),
        (Decimal(27), 6, Decimal(24), 5),
        (Decimal(28), 7, Decimal(24), 5),
        (Decimal(29), 8, Decimal(24), 5),
        (Decimal(30), 9, Decimal(24), 5),
        (Decimal(31), 10, Decimal(24), 5),
        (Decimal(32), 11, Decimal(24), 5),
        (Decimal(33), 12, Decimal(24), 5),
        (Decimal(34), 13, Decimal(24), 5),
        (Decimal(35), 14, Decimal(24), 5),
    ]


def test_final_candle_extremes_count_before_finalization() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    final = outcome_for(snapshots, frames[4].signal_id)[-1]
    assert final.mfe_price == Decimal(35)  # the final candle's own high


def test_partial_extremes_cover_observed_candles_only() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    versions = outcome_for(snapshots, frames[8].signal_id)
    last = versions[-1]
    assert last.status is OutcomeStatus.OPEN
    assert last.candles_observed == 8
    assert (last.mfe_price, last.mfe_index) == (Decimal(37), 16)
    assert (last.mae_price, last.mae_index) == (Decimal(28), 9)
    for version in versions:
        assert version.mfe_index is None or 9 <= version.mfe_index <= 16


def test_equal_extremes_keep_their_first_occurrence() -> None:
    frames = signal_frames(tie_tail_candles())
    snapshots = run(frames=frames, config=OutcomeTrackingConfig(horizon_bars=3))
    versions = outcome_for(snapshots, frames[4].signal_id)
    final = versions[-1]
    assert final.status is OutcomeStatus.LOSS
    assert final.final_close == Decimal(23)
    assert (versions[1].mfe_price, versions[1].mfe_index) == (Decimal(25), 5)
    assert (versions[2].mfe_price, versions[2].mfe_index) == (Decimal(25), 5)
    assert (versions[2].mae_price, versions[2].mae_index) == (Decimal(21), 6)
    assert (versions[3].mae_price, versions[3].mae_index) == (Decimal(21), 6)
    assert (final.mfe_price, final.mfe_index) == (Decimal(25), 5)
    assert (final.mae_price, final.mae_index) == (Decimal(21), 6)


def test_extremes_freeze_on_the_finalizing_version() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    versions = outcome_for(snapshots, frames[4].signal_id)
    final = versions[-1]
    assert final.status is OutcomeStatus.WIN
    assert all(version.candles_observed <= final.candles_observed for version in versions)
    assert (final.mfe_price, final.mfe_index) == (Decimal(35), 14)
    assert (final.mae_price, final.mae_index) == (Decimal(24), 5)


def test_falling_tail_extremes_match_hand_computed_geometry() -> None:
    falling = signal_frames(candles_for(FALLING_TAIL))
    snapshots = run(frames=falling)
    completed = [record for snapshot in snapshots for record in snapshot.completed]
    assert len(completed) == 1
    record = completed[0]
    assert record.status is OutcomeStatus.LOSS
    assert record.final_close == Decimal(14) and record.final_index == 14
    assert (record.mfe_price, record.mfe_index) == (Decimal(24), 5)
    assert (record.mae_price, record.mae_index) == (Decimal(13), 14)


def test_exact_decimal_sign_survives_where_floats_collapse() -> None:
    above = Decimal("24.0000000000000000000000000000000000000001")
    below = Decimal("23.9999999999999999999999999999999999999999")
    assert above != Decimal(24) and below != Decimal(24)
    assert float(above) == float(Decimal(24))
    for close, expected in (
        (above, OutcomeStatus.WIN),
        (below, OutcomeStatus.LOSS),
        (Decimal(24), OutcomeStatus.FLAT),
    ):
        assert classify_outcome(final_difference(close, Decimal(24))) is expected


def test_epsilon_pipeline_outcomes_classify_by_exact_sign() -> None:
    for final_close, expected in (
        (Decimal("24.0000000000000000000000000000000000000001"), OutcomeStatus.WIN),
        (Decimal(24), OutcomeStatus.FLAT),
        (Decimal("23.9999999999999999999999999999999999999999"), OutcomeStatus.LOSS),
    ):
        frames = signal_frames(epsilon_tail_candles(final_close))
        snapshots = run(frames=frames, config=OutcomeTrackingConfig(horizon_bars=3))
        versions = outcome_for(snapshots, frames[4].signal_id)
        assert versions[-1].status is expected
        assert versions[-1].final_close == final_close


def test_return_ratios_are_descriptive_and_hand_checkable() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    final = outcome_for(snapshots, frames[4].signal_id)[-1]
    assert mfe_return(final) == ratio(Decimal(11), Decimal(24))
    assert mae_return(final) == Decimal(0)
    assert final_return(final) == ratio(Decimal(10), Decimal(24))
    open_version = outcome_for(snapshots, frames[8].signal_id)[-1]
    assert mfe_return(open_version) == ratio(Decimal(9), Decimal(28))
    assert mae_return(open_version) == Decimal(0)
    assert final_return(open_version) is None
    created = outcome_for(snapshots, frames[16].signal_id)[0]
    assert mfe_return(created) is None and mae_return(created) is None


def test_extreme_indices_must_stay_inside_the_observed_window() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    final = outcome_for(snapshots, frames[4].signal_id)[-1]
    inside = replace(final, mfe_index=final.reference.candle_index + final.candles_observed)
    assert inside.mfe_index == 14
    with pytest.raises(AnalysisInputError):
        replace(final, mfe_index=final.reference.candle_index + final.candles_observed + 1)
