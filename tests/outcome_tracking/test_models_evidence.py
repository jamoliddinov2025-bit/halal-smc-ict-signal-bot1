from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError, OutcomeTrackingConfig, SignalStatus
from smcsignal.analysis.outcome_tracking.config import METHODOLOGY_VERSION
from smcsignal.analysis.outcome_tracking.evidence import (
    configuration_artifact,
    outcome_identity,
)
from smcsignal.analysis.outcome_tracking.models import (
    AnalyticsSummary,
    OutcomeStatus,
)
from tests.outcome_tracking.helpers import (
    completed_records,
    outcome_for,
    run,
    signal_frames,
)


def finalized_record():
    record = completed_records(run())[0]
    assert record.status is OutcomeStatus.WIN
    return record


def test_open_record_cannot_carry_finals() -> None:
    record = finalized_record()
    with pytest.raises(AnalysisInputError):
        replace(
            record,
            status=OutcomeStatus.OPEN,
            final_index=None,
            final_close=None,
            finalized_available_at=None,
        )


def test_final_record_requires_finals_and_horizon_alignment() -> None:
    record = finalized_record()
    with pytest.raises(AnalysisInputError):
        replace(record, final_close=None, final_index=None)
    with pytest.raises(AnalysisInputError):
        replace(record, status=OutcomeStatus.LOSS)
    with pytest.raises(AnalysisInputError):
        replace(record, status=OutcomeStatus.FLAT)
    with pytest.raises(AnalysisInputError):
        replace(record, candles_observed=record.horizon_bars - 1)


@pytest.mark.parametrize(
    ("final_close", "status"),
    [
        (Decimal("30"), OutcomeStatus.WIN),
        (Decimal("24"), OutcomeStatus.FLAT),
        (Decimal("20"), OutcomeStatus.LOSS),
    ],
)
def test_status_must_match_the_exact_sign(final_close: Decimal, status: OutcomeStatus) -> None:
    record = finalized_record()
    assert replace(record, final_close=final_close, status=status) is not None
    flipped = {
        OutcomeStatus.WIN: OutcomeStatus.LOSS,
        OutcomeStatus.LOSS: OutcomeStatus.WIN,
        OutcomeStatus.FLAT: OutcomeStatus.WIN,
    }[status]
    with pytest.raises(AnalysisInputError):
        replace(record, final_close=final_close, status=flipped)


def test_extremes_exist_exactly_once_evaluation_starts() -> None:
    record = finalized_record()
    with pytest.raises(AnalysisInputError):
        replace(
            record,
            candles_observed=0,
            mfe_price=None,
            mfe_index=None,
            mae_price=None,
            mae_index=None,
            evaluated_available_at=None,
        )
    with pytest.raises(AnalysisInputError):
        replace(record, mfe_index=None)
    with pytest.raises(AnalysisInputError):
        replace(record, mae_price=None)
    with pytest.raises(AnalysisInputError):
        replace(record, mfe_index=record.reference.candle_index)


def test_prices_must_be_positive_and_finite() -> None:
    record = finalized_record()
    for field, value in (
        ("reference_close", Decimal("0")),
        ("reference_close", Decimal("-1")),
        ("reference_close", Decimal("NaN")),
        ("final_close", Decimal("0")),
        ("mfe_price", Decimal("-5")),
    ):
        with pytest.raises(AnalysisInputError):
            replace(record, **{field: value})


def test_record_configuration_and_identity_are_required() -> None:
    record = finalized_record()
    with pytest.raises(AnalysisInputError):
        replace(record, settings=OutcomeTrackingConfig(horizon_bars=record.horizon_bars + 1))
    with pytest.raises(AnalysisInputError):
        replace(record, horizon_bars=record.horizon_bars + 1)
    with pytest.raises(AnalysisInputError):
        replace(record, signal_id="  ")
    with pytest.raises(AnalysisInputError):
        replace(record, outcome_id="")


def test_provenance_must_be_the_outcome_record_contract() -> None:
    record = finalized_record()
    assert record.provenance.producer == "outcome-record"
    assert record.provenance.producer_version == "1"
    assert record.provenance.series == record.reference.series
    assert record.reference in record.provenance.source_candles
    with pytest.raises(AnalysisInputError):
        replace(record, provenance=replace(record.provenance, producer="other"))


def test_analytics_summary_count_invariants() -> None:
    summary = AnalyticsSummary(
        total_buy_signals=3,
        open_count=1,
        win_count=1,
        loss_count=1,
        flat_count=0,
        finalized_count=2,
        final_return_sum=Decimal("0.5"),
        mfe_return_sum=Decimal("1"),
        mae_return_sum=Decimal("-1"),
        win_rate=Decimal("0.5"),
        average_final_return=Decimal("0.25"),
        average_mfe_return=Decimal("0.5"),
        average_mae_return=Decimal("-0.5"),
    )
    assert summary.win_count + summary.loss_count + summary.flat_count == 2
    with pytest.raises(AnalysisInputError):
        replace(summary, win_count=2)
    with pytest.raises(AnalysisInputError):
        replace(summary, open_count=2)
    with pytest.raises(AnalysisInputError):
        replace(summary, win_rate=Decimal("1.5"))
    with pytest.raises(AnalysisInputError):
        replace(summary, final_return_sum=Decimal("NaN"))


def test_analytics_undefined_statistics_are_none_not_zero() -> None:
    summary = AnalyticsSummary(
        total_buy_signals=1,
        open_count=1,
        win_count=0,
        loss_count=0,
        flat_count=0,
        finalized_count=0,
        final_return_sum=Decimal(0),
        mfe_return_sum=Decimal(0),
        mae_return_sum=Decimal(0),
        win_rate=None,
        average_final_return=None,
        average_mfe_return=None,
        average_mae_return=None,
    )
    assert summary.win_rate is None
    with pytest.raises(AnalysisInputError):
        replace(summary, win_rate=Decimal(0))


def test_snapshot_requires_matching_upstream_contract() -> None:
    snapshots = run()
    buy_snapshot = snapshots[4]
    assert buy_snapshot.upstream.status is SignalStatus.BUY_SIGNAL
    with pytest.raises(AnalysisInputError):
        replace(buy_snapshot, created=())
    evaluating = snapshots[5]
    assert evaluating.evaluated and all(
        record.status is OutcomeStatus.OPEN for record in evaluating.evaluated
    )
    with pytest.raises(AnalysisInputError):
        replace(evaluating, completed=evaluating.evaluated)
    with pytest.raises(AnalysisInputError):
        replace(buy_snapshot, settings=OutcomeTrackingConfig(horizon_bars=3))


def test_snapshot_created_exactly_on_buy_frames() -> None:
    snapshots = run()
    for snapshot in snapshots:
        assert bool(snapshot.created) is (snapshot.upstream.status is SignalStatus.BUY_SIGNAL)
        for record in snapshot.created:
            assert record.status is OutcomeStatus.OPEN
            assert record.candles_observed == 0
            assert record.signal_id == snapshot.upstream.signal_id
        for record in snapshot.evaluated:
            assert record.candles_observed >= 1
        for record in snapshot.completed:
            assert record.status is not OutcomeStatus.OPEN


def test_outcome_identity_is_stable_across_versions_and_future_free() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    versions = outcome_for(snapshots, frames[4].signal_id)
    identities = {version.outcome_id for version in versions}
    assert len(identities) == 1
    assert identities == {outcome_identity(frames[4], OutcomeTrackingConfig())}
    assert versions[0].outcome_id.startswith("spot-outcome:")
    assert frames[4].signal_id not in versions[0].outcome_id


def test_every_version_has_a_distinct_evidence_id() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    versions = outcome_for(snapshots, frames[4].signal_id)
    ids = [version.provenance.evidence_id for version in versions]
    assert len(ids) == len(set(ids))
    assert all(evidence_id.startswith("outcome-record:") for evidence_id in ids)
    assert all(version.provenance.evidence_id != version.outcome_id for version in versions)


def test_records_reuse_the_current_consumed_prefix_and_keep_upstream_ids() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    for index, snapshot in enumerate(snapshots):
        assert snapshot.provenance.input_prefix_hash == frames[index].provenance.input_prefix_hash
        for record in (*snapshot.created, *snapshot.evaluated):
            assert record.provenance.input_prefix_hash == frames[index].provenance.input_prefix_hash
            assert record.signal_id == frames[index].signal_id or record in snapshot.evaluated
    created = [record for snapshot in snapshots for record in snapshot.created]
    assert {record.signal_id for record in created} == {
        frame.signal_id for frame in frames if frame.status is SignalStatus.BUY_SIGNAL
    }
    assert {record.setup_identity for record in created} == {
        frame.setup_identity for frame in frames if frame.status is SignalStatus.BUY_SIGNAL
    }


def test_snapshot_dependencies_cover_upstream_and_records() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    snapshot = snapshots[5]
    record_ids = {
        record.provenance.evidence_id for record in (*snapshot.created, *snapshot.evaluated)
    }
    dependency_ids = {item.evidence_id for item in snapshot.provenance.dependencies}
    assert frames[5].provenance.evidence_id in dependency_ids
    assert record_ids <= dependency_ids
    assert snapshot.upstream is frames[5]


def test_configuration_artifact_declares_every_non_goal() -> None:
    artifact = configuration_artifact(OutcomeTrackingConfig())
    text = artifact.decode("utf-8")
    assert METHODOLOGY_VERSION in text
    for non_goal in (
        "execution",
        "entry",
        "exit",
        "stop",
        "target",
        "fees",
        "slippage",
        "position_sizing",
        "sell",
        "short_trade",
        "optimization",
    ):
        assert f'"{non_goal}":false' in text
