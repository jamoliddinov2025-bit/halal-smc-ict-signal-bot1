from copy import deepcopy

import pytest

from smcsignal.analysis.halal_filter import analyze_halal
from smcsignal.analysis.mtf import analyze_mtf
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import EligibilityStatus, analyze_signal_eligibility
from smcsignal.analysis.signal_engine import SignalStatus, analyze_signal_engine
from smcsignal.analysis.signal_engine.models import current_observation
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.mtf.helpers import EIGHT, bars, four_hour_frames, hour_frames, ote_frames
from tests.signal_eligibility.helpers import run as eligibility_run
from tests.signal_engine.helpers import analyzer, matching_config, run


@pytest.mark.parametrize("cut", range(18))
def test_every_prefix_equals_corresponding_full_output_including_ids(cut):
    frames = eligibility_run(sqs_config=SetupQualityConfig(10))
    full = run(frames)
    prefix = run(frames[:cut])
    assert prefix == full[:cut]
    assert [frame.provenance.evidence_id for frame in prefix] == [
        frame.provenance.evidence_id for frame in full[:cut]
    ]
    assert [frame.candidate.provenance.evidence_id for frame in prefix] == [
        frame.candidate.provenance.evidence_id for frame in full[:cut]
    ]
    assert [frame.status for frame in prefix] == [frame.status for frame in full[:cut]]
    assert [frame.setup_identity for frame in prefix] == [
        frame.setup_identity for frame in full[:cut]
    ]
    assert [frame.signal_id for frame in prefix] == [frame.signal_id for frame in full[:cut]]


def test_unknown_asset_prefixes_remain_no_signal():
    frames = analyze_signal_eligibility(analyze_setup_quality(halal_run(mtf_for("ADAUSDT"))))
    full = analyze_signal_engine(frames, matching_config(frames[0]))
    for cut in (0, 1, 8, len(frames)):
        prefix = analyze_signal_engine(frames[:cut], matching_config(frames[0]))
        assert prefix == full[:cut]
        assert all(frame.status is SignalStatus.NO_SIGNAL for frame in prefix)


def test_future_mtf_frames_cannot_rewrite_historical_signals():
    original = mtf_for()
    full = run(analyze_signal_eligibility(analyze_setup_quality(analyze_halal(original))))
    mixed_ote = ote_frames(
        (
            *[frame.upstream.upstream.observation.candle for frame in original[:8]],
            *bars("15m", tuple(range(40, 49)), start=EIGHT.replace(hour=10)),
        ),
        "15m",
    )
    changed = run(
        analyze_signal_eligibility(
            analyze_setup_quality(
                analyze_halal(
                    analyze_mtf(mixed_ote, {"1h": hour_frames(), "4h": four_hour_frames()})
                )
            )
        )
    )
    assert [frame.provenance.evidence_id for frame in changed[:8]] == [
        frame.provenance.evidence_id for frame in full[:8]
    ]
    assert [frame.signal_id for frame in changed[:8]] == [frame.signal_id for frame in full[:8]]
    assert [frame.status for frame in changed[:8]] == [frame.status for frame in full[:8]]
    assert [frame.setup_identity for frame in changed[:8]] == [
        frame.setup_identity for frame in full[:8]
    ]


def test_retained_snapshots_never_mutate_after_future_updates():
    raw = eligibility_run(sqs_config=SetupQualityConfig(10))
    engine = analyzer(matching_config(raw[0]))
    retained = tuple(engine.update(frame) for frame in raw[:10])
    saved = deepcopy(retained)
    for frame in raw[10:]:
        engine.update(frame)
    assert retained == saved == run(raw)[:10]
    assert retained[4].status is SignalStatus.BUY_SIGNAL


def test_prefix_hash_is_the_consumed_eligibility_prefix_not_a_future_file_hash():
    frames = run()
    raw = eligibility_run()
    assert frames[0].provenance.input_prefix_hash == raw[0].provenance.input_prefix_hash
    assert frames[-1].provenance.input_prefix_hash == raw[-1].provenance.input_prefix_hash
    assert frames[0].provenance.input_prefix_hash != frames[-1].provenance.input_prefix_hash


def test_publication_is_exactly_at_the_closed_candle_availability_boundary():
    for frame in run(sqs_config=SetupQualityConfig(10)):
        observation = current_observation(frame.upstream)
        item = frame.candidate.signal
        assert item.published_at == observation.available_at
        assert item.published_at >= item.candle_closed_at
        assert item.candle_opened_at == observation.reference.opened_at
        assert item.candle_closed_at == observation.reference.closed_at
        assert item.eligibility_available_at <= item.published_at
        assert item.evidence_available_at <= item.published_at
        for reference in frame.candidate.evidence:
            assert reference.available_at <= item.published_at


def test_future_evidence_cannot_be_attached_to_an_already_published_signal():
    raw = eligibility_run(sqs_config=SetupQualityConfig(10))
    published = analyze_signal_engine(raw[:5], matching_config(raw[0]))[-1]
    later = run(raw)
    assert published == later[4]
    assert published.candidate.evidence == later[4].candidate.evidence
    cutoff = published.candidate.signal.published_at
    published_ids = {item.evidence_id for item in published.candidate.evidence}
    for reference in published.candidate.evidence:
        assert reference.available_at <= cutoff
    later_ids = {item.evidence_id for item in later[-1].candidate.evidence}
    for evidence_id in later_ids - published_ids:
        assert evidence_id not in published.setup_identity
    assert published.upstream.status is EligibilityStatus.ELIGIBLE
    assert published.status is SignalStatus.BUY_SIGNAL
