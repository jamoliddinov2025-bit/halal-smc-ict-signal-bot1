from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from smcsignal.analysis import (
    AnalysisInputError,
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)
from smcsignal.analysis.setup_quality import analyze_setup_quality
from smcsignal.analysis.signal_eligibility import analyze_signal_eligibility
from smcsignal.analysis.signal_engine import SignalEngineConfig, analyze_signal_engine
from tests.analysis.helpers import bar
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.outcome_tracking.helpers import (
    buy_indices,
    candles_for,
    outcome_for,
    run,
    signal_frames,
)


@pytest.mark.parametrize("cut", range(18))
def test_every_prefix_equals_the_full_series_prefix(cut: int) -> None:
    frames = signal_frames()
    assert analyze_outcome_tracking(frames[:cut]) == run(frames=frames)[:cut]


def test_future_price_shocks_cannot_change_finalized_outcomes() -> None:
    base_frames = signal_frames()
    shocked_frames = signal_frames(candles_for((*range(20, 35), 100, 101)))
    assert base_frames[4].signal_id == shocked_frames[4].signal_id
    base = run(frames=base_frames)
    shocked = run(frames=shocked_frames)
    base_versions = outcome_for(base, base_frames[4].signal_id)
    shocked_versions = outcome_for(shocked, shocked_frames[4].signal_id)
    assert base_versions == shocked_versions
    assert [version.provenance.evidence_id for version in base_versions] == [
        version.provenance.evidence_id for version in shocked_versions
    ]
    assert base_versions[-1].final_index == 14


def test_shock_inside_the_horizon_changes_only_later_versions() -> None:
    base_frames = signal_frames()
    shocked_frames = signal_frames(candles_for((*range(20, 30), Decimal("50"), *range(31, 37))))
    assert base_frames[:10] == shocked_frames[:10]
    assert analyze_outcome_tracking(base_frames[:10]) == analyze_outcome_tracking(
        shocked_frames[:10]
    )
    base = run(frames=base_frames)
    shocked = run(frames=shocked_frames)
    assert base[:10] == shocked[:10]
    base_versions = outcome_for(base, base_frames[4].signal_id)
    shocked_versions = outcome_for(shocked, shocked_frames[4].signal_id)
    assert base_versions[:5] == shocked_versions[:5]
    assert base_versions[5:] != shocked_versions[5:]


def test_published_snapshots_are_immutable_under_continuation() -> None:
    frames = signal_frames()
    tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig())
    retained = tuple(tracker.update(frame) for frame in frames[:12])
    saved = deepcopy(retained)
    for frame in frames[12:]:
        tracker.update(frame)
    assert retained == saved
    assert [snapshot.provenance.evidence_id for snapshot in retained] == [
        snapshot.provenance.evidence_id for snapshot in saved
    ]


@pytest.mark.parametrize("value", [None, {}, "data", True, bar(0, 100)])
def test_invalid_inputs_are_atomic(value: object) -> None:
    frames = signal_frames()
    tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig())
    before = tuple(tracker.update(frame) for frame in frames[:5])
    state = (
        tracker.latest,
        tracker.processed_count,
        tracker.open_outcomes,
        tracker.finalized_outcomes,
        tracker.configuration_artifact,
    )
    with pytest.raises(AnalysisInputError):
        tracker.update(value)
    assert (
        tracker.latest,
        tracker.processed_count,
        tracker.open_outcomes,
        tracker.finalized_outcomes,
        tracker.configuration_artifact,
    ) == state
    assert (*before, tracker.update(frames[5])) == run(frames=frames[:6])


def test_shifted_or_duplicate_indices_are_rejected() -> None:
    frames = signal_frames()
    tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig())
    with pytest.raises(AnalysisInputError):
        tracker.update(frames[1])
    tracker.update(frames[0])
    with pytest.raises(AnalysisInputError):
        tracker.update(frames[0])
    assert tracker.processed_count == 1
    assert tracker.update(frames[1]) is not None


def test_series_cannot_change_mid_stream() -> None:
    frames = signal_frames()
    other_source = halal_run(mtf_for("ETHUSDT"))
    scored = analyze_setup_quality(other_source)
    eligible = analyze_signal_eligibility(scored)
    other = analyze_signal_engine(
        eligible,
        SignalEngineConfig(publish_threshold=eligible[0].decision.eligibility.publish_threshold),
    )
    tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig())
    tracker.update(frames[0])
    with pytest.raises(AnalysisInputError, match="series cannot change"):
        tracker.update(other[1])


def test_mixed_signal_engine_configurations_are_rejected() -> None:
    threshold10 = signal_frames()
    threshold75 = signal_frames(threshold=75)
    assert threshold10[0].settings != threshold75[0].settings
    tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig())
    tracker.update(threshold10[0])
    with pytest.raises(AnalysisInputError, match="one signal engine configuration"):
        tracker.update(threshold75[1])


def test_outcome_ids_never_depend_on_future_candles() -> None:
    frames = signal_frames()
    base = run(frames=frames)
    created = [record for snapshot in base for record in snapshot.created]
    assert [record.outcome_id for record in created] == [
        outcome_for(base, frames[index].signal_id)[0].outcome_id for index in buy_indices(frames)
    ]
