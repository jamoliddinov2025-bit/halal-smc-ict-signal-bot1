from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from smcsignal.analysis import AnalysisInputError, OutcomeTrackingConfig
from smcsignal.analysis.outcome_tracking.calculation import aggregate
from smcsignal.analysis.outcome_tracking.models import OutcomeStatus
from tests.outcome_tracking.helpers import (
    FLAT_TAIL,
    candles_for,
    completed_records,
    ratio,
    run,
    signal_frames,
)


def exact_sum(values: list[Decimal]) -> Decimal:
    """Independent exact addition with generous guard precision."""

    if not values:
        return Decimal(0)
    with localcontext() as context:
        context.prec = max(500, max(len(value.as_tuple().digits) for value in values) + 60)
        return sum(values, Decimal(0))


def independent_expected(records):
    finals = [record for record in records if record.status is not OutcomeStatus.OPEN]
    wins = sum(record.status is OutcomeStatus.WIN for record in finals)
    losses = sum(record.status is OutcomeStatus.LOSS for record in finals)
    flats = sum(record.status is OutcomeStatus.FLAT for record in finals)
    count = Decimal(len(finals))
    final_sum = exact_sum(
        [
            ratio(record.final_close - record.reference_close, record.reference_close)
            for record in finals
        ]
    )
    mfe_sum = exact_sum(
        [
            ratio(record.mfe_price - record.reference_close, record.reference_close)
            for record in finals
        ]
    )
    mae_sum = exact_sum(
        [
            ratio(record.mae_price - record.reference_close, record.reference_close)
            for record in finals
        ]
    )
    return finals, wins, losses, flats, count, final_sum, mfe_sum, mae_sum


def test_zero_signal_stream_is_a_valid_analytics_outcome() -> None:
    frames = signal_frames(threshold=75)
    assert all(frame.status.value == "NO_SIGNAL" for frame in frames)
    snapshots = run(frames=frames)
    for snapshot in snapshots:
        summary = snapshot.analytics
        assert summary.total_buy_signals == 0
        assert summary.open_count == 0
        assert summary.finalized_count == 0
        assert summary.win_count == 0 and summary.loss_count == 0 and summary.flat_count == 0
        assert summary.final_return_sum == 0 and summary.mfe_return_sum == 0
        assert summary.mae_return_sum == 0
        assert summary.win_rate is None
        assert summary.average_final_return is None
        assert summary.average_mfe_return is None and summary.average_mae_return is None
        assert snapshot.created == () and snapshot.evaluated == ()


def test_open_only_stream_reports_undefined_statistics() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames, config=OutcomeTrackingConfig(horizon_bars=13))
    last = snapshots[-1].analytics
    assert last.total_buy_signals == 4
    assert last.open_count == 4 and last.finalized_count == 0
    assert last.win_rate is None
    assert last.average_final_return is None
    assert last.final_return_sum == 0


def test_flat_tail_aggregates_recompute_from_raw_prices() -> None:
    frames = signal_frames(candles_for(FLAT_TAIL))
    snapshots = run(frames=frames)
    completed = completed_records(snapshots)
    assert [record.status for record in completed] == [OutcomeStatus.FLAT]
    summary = snapshots[-1].analytics
    _, wins, losses, flats, count, final_sum, mfe_sum, mae_sum = independent_expected(completed)
    assert (wins, losses, flats) == (0, 0, 1)
    assert summary.total_buy_signals == 7
    assert summary.open_count == 6 and summary.finalized_count == 1
    assert summary.win_rate == ratio(Decimal(0), count) == Decimal(0)
    assert summary.final_return_sum == final_sum == 0
    assert summary.mfe_return_sum == mfe_sum == ratio(Decimal(3), Decimal(24))
    assert summary.mae_return_sum == mae_sum == ratio(Decimal(-3), Decimal(24))
    assert summary.average_final_return == ratio(final_sum, count)
    assert summary.average_mfe_return == ratio(mfe_sum, count)


def test_mixed_statuses_recompute_every_aggregate() -> None:
    frames = signal_frames(candles_for(FLAT_TAIL))
    snapshots = run(frames=frames, config=OutcomeTrackingConfig(horizon_bars=3))
    completed = completed_records(snapshots)
    statuses = [record.status for record in completed]
    assert statuses.count(OutcomeStatus.WIN) == 3
    assert statuses.count(OutcomeStatus.LOSS) == 2
    assert statuses.count(OutcomeStatus.FLAT) == 0
    summary = snapshots[-1].analytics
    assert summary.total_buy_signals == 7
    assert summary.finalized_count == 5 and summary.open_count == 2
    finals, wins, losses, flats, count, final_sum, mfe_sum, mae_sum = independent_expected(
        completed
    )
    assert (summary.win_count, summary.loss_count, summary.flat_count) == (wins, losses, flats)
    assert summary.final_return_sum == final_sum
    assert summary.mfe_return_sum == mfe_sum
    assert summary.mae_return_sum == mae_sum
    assert summary.win_rate == ratio(Decimal(wins), count)
    assert summary.average_final_return == ratio(final_sum, count)
    assert summary.average_mfe_return == ratio(mfe_sum, count)
    assert summary.average_mae_return == ratio(mae_sum, count)


def test_per_frame_analytics_recompute_from_cumulative_records() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    cumulative: dict = {}
    for snapshot in snapshots:
        for record in snapshot.created:
            cumulative[record.outcome_id] = record
        for record in snapshot.completed:
            cumulative[record.outcome_id] = record
        records = list(cumulative.values())
        summary = snapshot.analytics
        finals, wins, losses, flats, count, final_sum, mfe_sum, mae_sum = independent_expected(
            records
        )
        assert summary.total_buy_signals == len(records)
        assert summary.finalized_count == len(finals)
        assert (summary.win_count, summary.loss_count, summary.flat_count) == (
            wins,
            losses,
            flats,
        )
        assert summary.open_count == len(records) - len(finals)
        assert summary.final_return_sum == final_sum
        assert summary.mfe_return_sum == mfe_sum
        assert summary.mae_return_sum == mae_sum


def test_open_outcomes_contribute_only_their_existence() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    last = snapshots[-1].analytics
    assert last.open_count == 3
    open_records = [
        record for record in snapshots[-1].evaluated if record.status is OutcomeStatus.OPEN
    ]
    assert len(open_records) == 2
    assert any(record.mfe_price is not None for record in open_records)
    assert last.mfe_return_sum == ratio(Decimal(11), Decimal(24))
    assert last.mae_return_sum == Decimal(0)
    assert last.final_return_sum == ratio(Decimal(10), Decimal(24))
    assert last.win_rate == Decimal(1)


def test_aggregate_rejects_incoherent_inputs() -> None:
    record = completed_records(run())[0]
    with pytest.raises(AnalysisInputError):
        aggregate((record,), total_buy_signals=2, open_count=0)
    with pytest.raises(AnalysisInputError):
        aggregate((record,), total_buy_signals=1, open_count=1)
    with pytest.raises(AnalysisInputError):
        aggregate((), total_buy_signals=-1, open_count=0)
    with pytest.raises(AnalysisInputError):
        aggregate((), total_buy_signals=0, open_count=-1)
    opened = [snapshot for snapshot in run() if snapshot.created][0].created[0]
    with pytest.raises(AnalysisInputError):
        aggregate((opened,), total_buy_signals=1, open_count=0)


def test_aggregate_adversarial_decimals_stay_exact() -> None:
    base = completed_records(run())[0]
    weird = [
        replace(
            base,
            reference_close=Decimal("0.3"),
            final_close=Decimal("0.1"),
            mfe_price=Decimal("0.7"),
            mae_price=Decimal("0.05"),
            status=OutcomeStatus.LOSS,
        ),
        replace(
            base,
            reference_close=Decimal("7"),
            final_close=Decimal("10"),
            mfe_price=Decimal("11"),
            mae_price=Decimal("6"),
            status=OutcomeStatus.WIN,
        ),
    ]
    summary = aggregate(tuple(weird), total_buy_signals=2, open_count=0)
    assert summary.win_count == 1 and summary.loss_count == 1
    expected = exact_sum(
        [
            ratio(Decimal("0.1") - Decimal("0.3"), Decimal("0.3")),
            ratio(Decimal(3), Decimal(7)),
        ]
    )
    assert summary.final_return_sum == expected
    assert summary.win_rate == ratio(Decimal(1), Decimal(2))
    assert summary.average_final_return == ratio(expected, Decimal(2))
