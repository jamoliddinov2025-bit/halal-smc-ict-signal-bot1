from __future__ import annotations

from copy import deepcopy

from smcsignal.analysis import (
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
    SignalStatus,
    analyze_outcome_tracking,
)
from smcsignal.analysis.setup_quality import SetupQualityConfig
from tests.outcome_tracking.helpers import buy_indices, run, signal_frames
from tests.signal_engine.helpers import run as signal_run


def test_helper_chain_matches_the_phase_17_fixtures() -> None:
    assert signal_frames() == signal_run(sqs_config=SetupQualityConfig(10))


def test_tracking_preserves_upstream_frames_exactly() -> None:
    frames = signal_frames()
    saved = deepcopy(frames)
    snapshots = analyze_outcome_tracking(frames)
    assert frames == saved
    assert all(
        snapshot.upstream is original for snapshot, original in zip(snapshots, frames, strict=True)
    )
    assert [frame.signal_id for frame in frames] == [frame.signal_id for frame in saved]
    assert [frame.status for frame in frames] == [frame.status for frame in saved]


def test_wrapping_does_not_change_published_signal_facts() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    for index, snapshot in enumerate(snapshots):
        assert snapshot.upstream.status is frames[index].status
        assert snapshot.upstream.signal_id == frames[index].signal_id
        assert snapshot.upstream.setup_identity == frames[index].setup_identity
        assert snapshot.upstream.candidate is frames[index].candidate


def test_phase_17_duplicate_policy_still_holds_under_tracking() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    buys = [index for index, frame in enumerate(frames) if frame.status is SignalStatus.BUY_SIGNAL]
    assert buys == buy_indices(frames)
    identities = [frames[index].setup_identity for index in buys]
    assert len(identities) == len(set(identities))
    created = [record for snapshot in snapshots for record in snapshot.created]
    assert [record.setup_identity for record in created] == identities


def test_registry_latest_versions_match_snapshot_publications() -> None:
    frames = signal_frames()
    snapshots = run(frames=frames)
    published = {
        record.provenance.evidence_id
        for snapshot in snapshots
        for record in (*snapshot.created, *snapshot.evaluated)
    }
    tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig())
    for frame in frames:
        tracker.update(frame)
    registry_ids = {
        record.provenance.evidence_id
        for record in (*tracker.open_outcomes, *tracker.finalized_outcomes)
    }
    assert registry_ids < published
    assert len(registry_ids) == 4
