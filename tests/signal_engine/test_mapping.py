import json
from hashlib import sha256

from smcsignal.analysis.halal_filter import AssetClassification
from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_eligibility import (
    EligibilityStatus,
    MarketBias,
    SignalEligibility,
)
from smcsignal.analysis.signal_engine import (
    SignalDirection,
    SignalEngineConfig,
    SignalReason,
    SignalStatus,
    direction_for,
    status_for,
)
from smcsignal.analysis.signal_engine.calculation import reasons_for
from smcsignal.analysis.signal_engine.config import METHODOLOGY_VERSION
from smcsignal.analysis.signal_engine.evidence import configuration_artifact, setup_identity
from tests.signal_eligibility.helpers import run as eligibility_run
from tests.signal_engine.helpers import run


def test_status_mapping_consumes_existing_eligibility_only():
    not_halal = SignalEligibility(
        EligibilityStatus.NOT_ELIGIBLE,
        MarketBias.NEUTRAL,
        AssetClassification.UNKNOWN,
        0,
        False,
        75,
    )
    below = SignalEligibility(
        EligibilityStatus.NOT_ELIGIBLE,
        MarketBias.LONG_BIAS,
        AssetClassification.HALAL,
        25,
        False,
        75,
    )
    eligible_neutral = SignalEligibility(
        EligibilityStatus.ELIGIBLE,
        MarketBias.NEUTRAL,
        AssetClassification.HALAL,
        80,
        True,
        75,
    )
    eligible_long = SignalEligibility(
        EligibilityStatus.ELIGIBLE,
        MarketBias.LONG_BIAS,
        AssetClassification.HALAL,
        80,
        True,
        75,
    )
    eligible_short = SignalEligibility(
        EligibilityStatus.ELIGIBLE,
        MarketBias.SHORT_BIAS,
        AssetClassification.HALAL,
        80,
        True,
        75,
    )
    assert status_for(not_halal) is SignalStatus.NO_SIGNAL
    assert status_for(below) is SignalStatus.NO_SIGNAL
    assert status_for(eligible_neutral) is SignalStatus.NO_SIGNAL
    assert status_for(eligible_long) is SignalStatus.BUY_SIGNAL
    assert status_for(eligible_short) is SignalStatus.BEARISH_AVOID
    assert direction_for(SignalStatus.BUY_SIGNAL) is SignalDirection.LONG
    assert direction_for(SignalStatus.BEARISH_AVOID) is SignalDirection.NONE
    assert direction_for(SignalStatus.NO_SIGNAL) is SignalDirection.NONE


def test_bearish_avoid_reasons_never_claim_a_short_trade():
    frame = eligibility_run(sqs_config=SetupQualityConfig(10))[4]
    assert reasons_for(frame, SignalStatus.BEARISH_AVOID) == (
        SignalReason.HALAL_ASSET,
        SignalReason.SQS_THRESHOLD_PASSED,
        SignalReason.BEARISH_SPOT_AVOID,
    )


def test_setup_identity_omits_per_candle_frame_ids():
    frames = eligibility_run()
    first = setup_identity(frames[0])
    later = setup_identity(frames[4])
    assert first.startswith("spot-setup:")
    assert later != first
    assert frames[0].provenance.evidence_id not in first
    assert frames[4].provenance.evidence_id not in later
    assert setup_identity(frames[0]) == first


def test_identical_inputs_keep_identical_signal_identities():
    left = run()
    right = run()
    assert [frame.signal_id for frame in left] == [frame.signal_id for frame in right]
    assert [frame.setup_identity for frame in left] == [frame.setup_identity for frame in right]
    assert [frame.candidate.provenance.evidence_id for frame in left] == [
        frame.candidate.provenance.evidence_id for frame in right
    ]


def test_configuration_artifact_binds_spot_only_and_no_execution_policy():
    frames = run()
    payload = json.loads(configuration_artifact(SignalEngineConfig()))
    assert payload["methodology"] == METHODOLOGY_VERSION
    assert payload["spot_only"] is True
    assert payload["duplicate"] == "one_per_setup"
    assert payload["execution"] is False
    assert payload["leverage"] is False
    assert payload["futures"] is False
    assert payload["short_trade"] is False
    assert payload["entry"] is False
    assert payload["optimization"] is False
    digest = sha256(configuration_artifact(SignalEngineConfig())).hexdigest()
    for frame in frames:
        assert frame.provenance.configuration_hash == digest
        assert frame.candidate.provenance.configuration_hash == digest
        assert frame.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash


def test_evidence_references_are_not_from_the_future():
    for frame in run():
        cutoff = frame.provenance.available_at
        for reference in frame.candidate.evidence:
            assert reference.available_at <= cutoff
            assert reference.series == frame.provenance.series
        assert frame.candidate.signal.published_at >= frame.candidate.signal.candle_closed_at
        assert frame.candidate.signal.evidence_available_at <= frame.candidate.signal.published_at
