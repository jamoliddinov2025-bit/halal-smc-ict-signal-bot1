from __future__ import annotations

import pytest

from smcsignal.analysis import (
    AnalysisConfigurationError,
    AnalysisInputError,
    OutcomeStatus,
    OutcomeTrackingAnalyzer,
    analyze_outcome_tracking,
)
from tests.outcome_tracking.helpers import analyzer, run, signal_frames


def test_batch_and_stream_reuse_exact_upstream_objects() -> None:
    frames = signal_frames()
    tracker = analyzer()
    actual = tuple(tracker.update(frame) for frame in frames)
    expected = run(frames=frames)
    assert actual == expected
    assert all(
        snapshot.upstream is original for snapshot, original in zip(actual, frames, strict=True)
    )
    assert tracker.latest is actual[-1]
    assert tracker.processed_count == len(frames)
    assert tracker.series == frames[0].provenance.series
    assert tracker.configuration_artifact is not None


@pytest.mark.parametrize("cut", range(18))
def test_every_chunk_boundary_is_identical(cut: int) -> None:
    frames = signal_frames()
    tracker = analyzer()
    outputs = []
    for chunk in (frames[:cut], (), frames[cut:]):
        outputs.extend(tracker.update(frame) for frame in chunk)
    assert tuple(outputs) == run(frames=frames)


def test_many_small_chunks_remain_equivalent() -> None:
    frames = signal_frames()
    tracker = analyzer()
    outputs = []
    sizes = [1, 2, 3, 1, 4, 2, 1, 3]
    start = 0
    for size in sizes:
        outputs.extend(tracker.update(frame) for frame in frames[start : start + size])
        start += size
    outputs.extend(tracker.update(frame) for frame in frames[start:])
    assert tuple(outputs) == run(frames=frames)


def test_empty_and_single_pass_inputs() -> None:
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from signal_frames()

    assert analyze_outcome_tracking(Once()) == run()
    assert analyze_outcome_tracking(()) == ()
    tracker = analyzer()
    assert tracker.series is None and tracker.latest is None
    assert tracker.open_outcomes == () and tracker.finalized_outcomes == ()
    assert tracker.configuration_artifact is None


def test_noniterable_primary_fails() -> None:
    with pytest.raises(AnalysisInputError):
        analyze_outcome_tracking(None)


def test_interleaved_analyzers_do_not_share_state() -> None:
    frames = signal_frames()
    first, second = analyzer(), analyzer()
    for frame in frames:
        assert first.update(frame) == second.update(frame)


def test_constructor_rejects_foreign_configuration() -> None:
    with pytest.raises(AnalysisConfigurationError):
        OutcomeTrackingAnalyzer(config=object())
    with pytest.raises(AnalysisConfigurationError):
        analyze_outcome_tracking((), config=object())


def test_registry_views_track_the_stream_lifecycle() -> None:
    frames = signal_frames()
    tracker = analyzer()
    for count, frame in enumerate(frames, start=1):
        snapshot = tracker.update(frame)
        total_created = len(tracker.open_outcomes) + len(tracker.finalized_outcomes)
        assert total_created == snapshot.analytics.total_buy_signals
        assert len(tracker.open_outcomes) == snapshot.analytics.open_count
        assert len(tracker.finalized_outcomes) == snapshot.analytics.finalized_count
        assert all(record.status is OutcomeStatus.OPEN for record in tracker.open_outcomes)
        assert all(record.status is not OutcomeStatus.OPEN for record in tracker.finalized_outcomes)
        assert tracker.processed_count == count
