from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import analyze_signal_eligibility
from smcsignal.analysis.signal_engine import SignalStatus, build_signal_snapshot
from tests.halal_filter.helpers import run as halal_run
from tests.setup_quality.helpers import run as sqs_run
from tests.signal_eligibility.helpers import run as eligibility_run
from tests.signal_engine.helpers import matching_config, run


def test_upstream_eligibility_objects_and_ids_are_preserved():
    raw = eligibility_run()
    frames = run(raw)
    for frame, original in zip(frames, raw, strict=True):
        assert frame.upstream is original
        assert frame.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert frame.upstream.decision is original.decision
        assert frame.candidate.signal.score_total == original.decision.eligibility.score_total
        assert frame.candidate.signal.bias is original.bias
        assert frame.candidate.signal.classification is original.decision.eligibility.classification


def test_changing_only_the_sqs_threshold_renames_signal_ids_not_eligibility_ids():
    raw = eligibility_run()
    default = run(raw)
    lowered_upstream = analyze_signal_eligibility(
        analyze_setup_quality(halal_run(), SetupQualityConfig(10))
    )
    lowered = run(lowered_upstream)
    for left, right, original, lowered_original in zip(
        default, lowered, raw, lowered_upstream, strict=True
    ):
        assert left.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert right.upstream.provenance.evidence_id == lowered_original.provenance.evidence_id
        assert left.upstream.upstream.upstream.provenance.evidence_id == (
            right.upstream.upstream.upstream.provenance.evidence_id
        )
        assert left.provenance.evidence_id != right.provenance.evidence_id
        assert left.status is SignalStatus.NO_SIGNAL
    assert lowered[4].status is SignalStatus.BUY_SIGNAL
    assert default[4].status is SignalStatus.NO_SIGNAL


def test_every_prefix_equals_corresponding_full_output_including_ids():
    frames = eligibility_run()
    full = run(frames)
    for cut in range(len(frames) + 1):
        prefix = run(frames[:cut])
        assert prefix == full[:cut]
        assert [frame.provenance.evidence_id for frame in prefix] == [
            frame.provenance.evidence_id for frame in full[:cut]
        ]
        assert [frame.candidate.provenance.evidence_id for frame in prefix] == [
            frame.candidate.provenance.evidence_id for frame in full[:cut]
        ]


def test_phases_one_through_sixteen_remain_untouched_by_mapping():
    scored = sqs_run()
    eligible = analyze_signal_eligibility(scored)
    mapped = tuple(build_signal_snapshot(frame, matching_config(frame)) for frame in eligible)
    for score, gate, published in zip(scored, eligible, mapped, strict=True):
        assert published.upstream is gate
        assert gate.upstream is score
        assert gate.upstream.provenance.evidence_id == score.provenance.evidence_id
        assert published.candidate.signal.publish_threshold == score.settings.publish_threshold
        assert published.candidate.signal.score_total == score.total
