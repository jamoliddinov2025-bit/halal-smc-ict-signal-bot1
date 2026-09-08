import json
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.halal_filter import AssetClassification
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_eligibility import EligibilityStatus, MarketBias
from smcsignal.analysis.signal_engine import (
    Signal,
    SignalCandidate,
    SignalDirection,
    SignalEngineConfig,
    SignalReason,
    SignalSnapshot,
    SignalStatus,
    build_signal_snapshot,
)
from smcsignal.analysis.signal_engine.models import current_observation
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.setup_quality.helpers import run as sqs_run
from tests.signal_eligibility.helpers import run as eligibility_run
from tests.signal_engine.helpers import make_signal, run


@pytest.fixture
def frames():
    return run()


def test_enums_are_spot_only_and_never_a_short_trade():
    assert {item.value for item in SignalStatus} == {
        "BUY_SIGNAL",
        "BEARISH_AVOID",
        "NO_SIGNAL",
    }
    assert {item.value for item in SignalDirection} == {"LONG", "NONE"}
    assert "SHORT" not in {item.value for item in SignalDirection}
    assert "SELL" not in {item.value for item in SignalStatus}


def test_all_published_fields_are_frozen(frames):
    frame = frames[0]
    for obj in (frame, frame.candidate, frame.candidate.signal, frame.settings):
        for field in fields(obj):
            with pytest.raises(FrozenInstanceError):
                setattr(obj, field.name, getattr(obj, field.name))


def test_no_execution_or_probability_fields():
    forbidden = {
        "probability",
        "confidence",
        "weight",
        "rank",
        "entry",
        "stop_loss",
        "take_profit",
        "position_size",
        "target_signal_count",
        "win_rate",
        "expected_value",
        "side",
        "telegram",
    }
    for cls in (SignalEngineConfig, Signal, SignalCandidate, SignalSnapshot):
        assert not forbidden.intersection(field.name for field in fields(cls))


def test_new_models_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).series == frames[0].provenance.series
        assert frame.upstream.provenance.as_reference() in frame.provenance.dependencies
        assert frame.candidate.provenance.as_reference() in frame.provenance.dependencies
        assert frame.candidate.provenance.source_candles == frame.provenance.source_candles
        assert frame.provenance.producer == "signal-frame"
        assert frame.candidate.provenance.producer == "signal-candidate"
        assert frame.candidate.signal_id == frame.candidate.provenance.evidence_id
        assert frame.signal_id == frame.candidate.signal_id


def test_default_synthetic_frames_are_no_signal(frames):
    assert frames[0].status is SignalStatus.NO_SIGNAL
    assert frames[0].direction is SignalDirection.NONE
    assert frames[0].candidate.signal.eligibility_status is EligibilityStatus.NOT_ELIGIBLE
    assert frames[0].candidate.signal.bias is MarketBias.NEUTRAL
    assert SignalReason.SQS_BELOW_THRESHOLD in frames[0].candidate.reasons
    assert frames[4].status is SignalStatus.NO_SIGNAL
    assert frames[4].direction is SignalDirection.NONE
    assert frames[4].candidate.signal.bias is MarketBias.LONG_BIAS
    assert frames[4].candidate.signal.threshold_passed is False


def test_eligible_long_bias_constructs_a_buy_signal():
    frame = run(sqs_config=SetupQualityConfig(10))[4]
    item = frame.candidate.signal
    assert frame.status is SignalStatus.BUY_SIGNAL
    assert frame.direction is SignalDirection.LONG
    assert item.classification is AssetClassification.HALAL
    assert item.eligibility_status is EligibilityStatus.ELIGIBLE
    assert item.bias is MarketBias.LONG_BIAS
    assert item.threshold_passed is True
    assert item.score_total >= item.publish_threshold
    assert SignalReason.HALAL_ASSET in frame.candidate.reasons
    assert SignalReason.SQS_THRESHOLD_PASSED in frame.candidate.reasons
    assert SignalReason.LONG_BIAS in frame.candidate.reasons
    assert frame.candidate.evidence


def test_eligible_neutral_stays_no_signal():
    frame = run(sqs_config=SetupQualityConfig(10))[0]
    assert frame.status is SignalStatus.NO_SIGNAL
    assert frame.direction is SignalDirection.NONE
    assert frame.candidate.signal.eligibility_status is EligibilityStatus.ELIGIBLE
    assert frame.candidate.signal.bias is MarketBias.NEUTRAL
    assert SignalReason.NEUTRAL_BIAS in frame.candidate.reasons


def test_unknown_asset_is_hard_gated_to_no_signal():
    frames = run(eligibility_run(sqs_run(halal_run(mtf_for("ADAUSDT")))))
    assert all(frame.status is SignalStatus.NO_SIGNAL for frame in frames)
    assert all(frame.direction is SignalDirection.NONE for frame in frames)
    assert all(frame.candidate.reasons == (SignalReason.NOT_HALAL,) for frame in frames)


def test_bearish_avoid_construction_never_carries_a_long_or_short_trade():
    source = eligibility_run()[0]
    item = make_signal(
        source,
        SignalStatus.BEARISH_AVOID,
        SignalDirection.NONE,
        eligibility_status=EligibilityStatus.ELIGIBLE,
        bias=MarketBias.SHORT_BIAS,
        threshold_passed=True,
        score_total=80,
        publish_threshold=75,
    )
    assert item.status is SignalStatus.BEARISH_AVOID
    assert item.direction is SignalDirection.NONE
    assert item.bias is MarketBias.SHORT_BIAS


def test_invalid_status_direction_combinations_are_rejected():
    source = eligibility_run(sqs_config=SetupQualityConfig(10))[4]
    with pytest.raises(AnalysisInputError):
        make_signal(source, SignalStatus.BUY_SIGNAL, SignalDirection.NONE)
    with pytest.raises(AnalysisInputError):
        make_signal(
            source,
            SignalStatus.BUY_SIGNAL,
            SignalDirection.LONG,
            bias=MarketBias.SHORT_BIAS,
        )
    with pytest.raises(AnalysisInputError):
        make_signal(
            source,
            SignalStatus.BEARISH_AVOID,
            SignalDirection.LONG,
            eligibility_status=EligibilityStatus.ELIGIBLE,
            bias=MarketBias.SHORT_BIAS,
            threshold_passed=True,
            score_total=80,
        )
    with pytest.raises(AnalysisInputError):
        make_signal(source, SignalStatus.NO_SIGNAL, SignalDirection.LONG)
    with pytest.raises(AnalysisInputError):
        make_signal(
            eligibility_run(sqs_run(halal_run(mtf_for("ADAUSDT"))))[0],
            SignalStatus.BUY_SIGNAL,
            SignalDirection.LONG,
        )


def test_publication_cannot_precede_candle_close_or_use_future_evidence():
    source = eligibility_run()[0]
    observation = current_observation(source)
    with pytest.raises(AnalysisInputError):
        make_signal(
            source,
            SignalStatus.NO_SIGNAL,
            SignalDirection.NONE,
            published_at=observation.available_at - timedelta(seconds=1),
        )
    with pytest.raises(AnalysisInputError):
        make_signal(
            source,
            SignalStatus.NO_SIGNAL,
            SignalDirection.NONE,
            evidence_available_at=observation.available_at + timedelta(seconds=1),
        )


def test_snapshot_rejects_mismatched_threshold_or_dependencies(frames):
    with pytest.raises(AnalysisInputError):
        build_signal_snapshot(frames[0].upstream, SignalEngineConfig(publish_threshold=10))
    other = frames[4]
    with pytest.raises(AnalysisInputError):
        replace(frames[0], candidate=other.candidate)
    with pytest.raises(AnalysisInputError):
        replace(frames[0], provenance=replace(frames[0].provenance, dependencies=()))


def test_full_serialization_retains_status_and_upstream_score(frames):
    data = json.loads(evidence_json(frames[4]))
    assert data["status"] == "NO_SIGNAL"
    assert data["direction"] == "NONE"
    assert data["candidate"]["signal"]["bias"] == "LONG_BIAS"
    assert data["candidate"]["signal"]["score_total"] == 25
    assert "entry" not in data
    assert "stop_loss" not in data
    buy = json.loads(evidence_json(run(sqs_config=SetupQualityConfig(10))[4]))
    assert buy["status"] == "BUY_SIGNAL"
    assert buy["direction"] == "LONG"


def test_package_source_does_not_fetch_optimize_or_emit_orders():
    root = Path(__file__).resolve().parents[2] / "src/smcsignal/analysis/signal_engine"
    text = "".join(path.read_text() for path in sorted(root.glob("*.py")))
    for needle in (
        "urllib",
        "http.client",
        "requests",
        "sklearn",
        "numpy",
        "telegram",
        "SELL",
        "win_rate",
        "stop_loss",
        "take_profit",
        "position_size",
    ):
        assert needle not in text
    assert "BUY_SIGNAL" in text
    assert "BEARISH_AVOID" in text
