from __future__ import annotations

import pytest

from smcsignal.analysis import AnalysisInputError, OutcomeStatus
from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_engine import build_signal_snapshot
from tests.outcome_tracking.helpers import analyzer, run, signal_frames
from tests.signal_eligibility.helpers import run as eligibility_run
from tests.signal_engine.helpers import matching_config


def test_replaying_a_tracked_buy_frame_is_rejected_atomically() -> None:
    frames = signal_frames()
    tracker = analyzer()
    for frame in frames[:6]:
        tracker.update(frame)
    state = (
        tracker.latest,
        tracker.processed_count,
        tracker.open_outcomes,
        tracker.finalized_outcomes,
    )
    with pytest.raises(AnalysisInputError, match="at most one outcome"):
        tracker.update(frames[4])
    assert (
        tracker.latest,
        tracker.processed_count,
        tracker.open_outcomes,
        tracker.finalized_outcomes,
    ) == state
    assert tracker.processed_count == 6


def test_duplicate_rejection_leaves_open_outcomes_intact() -> None:
    frames = signal_frames()
    tracker = analyzer()
    for frame in frames[:9]:
        tracker.update(frame)
    open_before = tracker.open_outcomes
    assert len(open_before) == 2
    with pytest.raises(AnalysisInputError):
        tracker.update(frames[4])
    assert tracker.open_outcomes == open_before
    continuation = tracker.update(frames[9])
    assert continuation.upstream is frames[9]
    assert continuation.created == () and continuation.evaluated


def test_distinct_signal_ids_with_one_setup_identity_stay_independent() -> None:
    upstream = eligibility_run(sqs_config=SetupQualityConfig(10))
    mapped = tuple(build_signal_snapshot(frame, matching_config(frame)) for frame in upstream)
    assert mapped[4].status is not None and mapped[4].status.value == "BUY_SIGNAL"
    assert mapped[5].status.value == "BUY_SIGNAL"
    assert mapped[4].setup_identity == mapped[5].setup_identity
    assert mapped[4].signal_id != mapped[5].signal_id
    snapshots = run(frames=mapped[:6])
    created = [record for snapshot in snapshots for record in snapshot.created]
    assert [record.signal_id for record in created] == [
        mapped[4].signal_id,
        mapped[5].signal_id,
    ]
    assert len({record.outcome_id for record in created}) == 2
    assert all(record.status is OutcomeStatus.OPEN for record in created)


def test_replaying_a_non_buy_frame_is_still_rejected() -> None:
    frames = signal_frames()
    tracker = analyzer()
    for frame in frames[:6]:
        tracker.update(frame)
    with pytest.raises(AnalysisInputError):
        tracker.update(frames[5])


def test_replays_are_deterministic_and_stateless_per_analyzer() -> None:
    frames = signal_frames()
    first, second = run(frames=frames), run(frames=frames)
    assert first == second
    tracker = analyzer()
    for frame in frames:
        tracker.update(frame)
    fresh = analyzer()
    prefix = tuple(fresh.update(frame) for frame in frames[:5])
    assert prefix == first[:5]
    assert prefix[4].created[0] == first[4].created[0]
    assert fresh.processed_count == 5 and tracker.processed_count == len(frames)
