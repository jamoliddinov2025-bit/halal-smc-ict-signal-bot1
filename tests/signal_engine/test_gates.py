from dataclasses import replace
from unittest.mock import patch

from smcsignal.analysis.halal_filter import AssetClassification, FilterMode, HalalFilterConfig
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    EligibilityStatus,
    MarketBias,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_engine import (
    SignalDirection,
    SignalReason,
    SignalStatus,
    analyze_signal_engine,
)
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.setup_quality.helpers import run as sqs_run
from tests.signal_eligibility.helpers import run as eligibility_run
from tests.signal_engine.helpers import matching_config, run


def _as_short_bias(frames):
    changed = []
    for frame in frames:
        if frame.bias is MarketBias.LONG_BIAS and frame.eligible:
            eligibility = replace(frame.decision.eligibility, bias=MarketBias.SHORT_BIAS)
            decision = replace(frame.decision, eligibility=eligibility)
            changed.append(replace(frame, decision=decision))
        else:
            changed.append(frame)
    return tuple(changed)


def test_eligible_long_bias_is_a_buy_signal():
    frame = run(sqs_config=SetupQualityConfig(10))[4]
    assert frame.upstream.status is EligibilityStatus.ELIGIBLE
    assert frame.upstream.bias is MarketBias.LONG_BIAS
    assert frame.status is SignalStatus.BUY_SIGNAL
    assert frame.direction is SignalDirection.LONG
    assert frame.candidate.signal.classification is AssetClassification.HALAL
    assert frame.candidate.signal.threshold_passed is True
    assert SignalReason.LONG_BIAS in frame.candidate.reasons


def test_eligible_short_bias_is_bearish_avoid_never_a_short():
    upstream = _as_short_bias(eligibility_run(sqs_config=SetupQualityConfig(10)))
    frame = analyze_signal_engine(upstream, matching_config(upstream[0]))[4]
    assert frame.upstream.bias is MarketBias.SHORT_BIAS
    assert frame.upstream.eligible is True
    assert frame.status is SignalStatus.BEARISH_AVOID
    assert frame.direction is SignalDirection.NONE
    assert SignalReason.BEARISH_SPOT_AVOID in frame.candidate.reasons
    assert frame.candidate.signal.classification is AssetClassification.HALAL


def test_eligible_neutral_is_no_signal():
    frame = run(sqs_config=SetupQualityConfig(10))[0]
    assert frame.upstream.eligible is True
    assert frame.upstream.bias is MarketBias.NEUTRAL
    assert frame.status is SignalStatus.NO_SIGNAL
    assert frame.direction is SignalDirection.NONE
    assert SignalReason.NEUTRAL_BIAS in frame.candidate.reasons


def test_haram_is_no_signal():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )
    upstream = analyze_signal_eligibility(
        analyze_setup_quality(halal_run(mtf_for("XYZUSDT"), config))
    )
    frames = analyze_signal_engine(upstream, matching_config(upstream[0]))
    assert frames[4].candidate.signal.classification is AssetClassification.HARAM
    assert all(frame.status is SignalStatus.NO_SIGNAL for frame in frames)
    assert all(frame.direction is SignalDirection.NONE for frame in frames)
    assert all(frame.candidate.reasons == (SignalReason.NOT_HALAL,) for frame in frames)


def test_unknown_is_no_signal():
    upstream = analyze_signal_eligibility(analyze_setup_quality(halal_run(mtf_for("ADAUSDT"))))
    frames = analyze_signal_engine(upstream, matching_config(upstream[0]))
    assert frames[0].candidate.signal.classification is AssetClassification.UNKNOWN
    assert all(frame.status is SignalStatus.NO_SIGNAL for frame in frames)
    assert all(frame.candidate.reasons == (SignalReason.NOT_HALAL,) for frame in frames)


def test_below_threshold_is_no_signal():
    frame = run()[4]
    assert frame.upstream.bias is MarketBias.LONG_BIAS
    assert frame.upstream.eligible is False
    assert frame.candidate.signal.threshold_passed is False
    assert frame.status is SignalStatus.NO_SIGNAL
    assert SignalReason.SQS_BELOW_THRESHOLD in frame.candidate.reasons


def test_not_eligible_is_no_signal():
    frame = run()[0]
    assert frame.upstream.status is EligibilityStatus.NOT_ELIGIBLE
    assert frame.status is SignalStatus.NO_SIGNAL
    assert frame.direction is SignalDirection.NONE


def test_missing_required_evidence_blocks_buy_signal():
    upstream = eligibility_run(sqs_config=SetupQualityConfig(10))
    with patch("smcsignal.analysis.signal_engine.calculation.collect_evidence", return_value=()):
        frames = analyze_signal_engine(upstream, matching_config(upstream[0]))
    assert upstream[4].status is EligibilityStatus.ELIGIBLE
    assert upstream[4].bias is MarketBias.LONG_BIAS
    assert frames[4].status is SignalStatus.NO_SIGNAL
    assert frames[4].direction is SignalDirection.NONE
    assert frames[4].candidate.reasons == (SignalReason.MISSING_REQUIRED_EVIDENCE,)


def test_analyzer_output_is_deterministic():
    left = run(sqs_config=SetupQualityConfig(10))
    right = run(sqs_config=SetupQualityConfig(10))
    assert left == right
    assert [frame.signal_id for frame in left] == [frame.signal_id for frame in right]
    assert [frame.setup_identity for frame in left] == [frame.setup_identity for frame in right]


def test_upstream_ids_and_provenance_are_preserved():
    raw = eligibility_run()
    frames = run(raw)
    scored = sqs_run()
    for published, original, score in zip(frames, raw, scored, strict=True):
        assert published.upstream is original
        assert published.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert original.upstream.provenance.evidence_id == score.provenance.evidence_id
        assert published.provenance.input_prefix_hash == original.provenance.input_prefix_hash
        assert published.candidate.signal.score_total == original.decision.eligibility.score_total
        assert published.candidate.signal.bias is original.bias
