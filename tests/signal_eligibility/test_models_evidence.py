import json
from dataclasses import FrozenInstanceError, fields, replace
from hashlib import sha256
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    EligibilityDecision,
    EligibilitySnapshot,
    EligibilityStatus,
    MarketBias,
    SignalEligibility,
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)
from tests.halal_filter.helpers import run as halal_run
from tests.setup_quality.helpers import run as sqs_run
from tests.signal_eligibility.helpers import analyzer, run


@pytest.fixture
def frames():
    return run()


def test_new_models_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).series == frames[0].provenance.series
        assert frame.upstream.provenance.as_reference() in frame.provenance.dependencies
        assert frame.decision.provenance.as_reference() in frame.provenance.dependencies
        assert frame.decision.provenance.source_candles == frame.provenance.source_candles
        assert frame.provenance.producer == "eligibility-frame"
        assert frame.decision.provenance.producer == "eligibility-decision"
        assert frame.upstream.provenance.as_reference() in frame.decision.evidence


def test_configuration_artifact_binds_no_trade_policy(frames):
    detector = analyzer()
    actual = tuple(detector.update(frame) for frame in sqs_run())
    payload = json.loads(detector.configuration_artifact)
    assert payload["methodology"] == "eligibility-v1"
    assert payload["gate"] == "halal_and_threshold"
    assert payload["conflict"] == "neutral"
    assert payload["missing"] == "abstain"
    assert payload["buy"] is False
    assert payload["sell"] is False
    assert payload["entry"] is False
    assert payload["optimization"] is False
    digest = sha256(detector.configuration_artifact).hexdigest()
    for frame in actual:
        assert frame.provenance.configuration_hash == digest
        assert frame.decision.provenance.configuration_hash == digest
        assert frame.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash


def test_full_serialization_retains_status_bias_and_score(frames):
    data = json.loads(evidence_json(frames[4]))
    assert data["status"] == "NOT_ELIGIBLE"
    assert data["bias"] == "LONG_BIAS"
    assert data["eligible"] is False
    assert data["decision"]["eligibility"]["score_total"] == 25
    assert data["decision"]["eligibility"]["classification"] == "HALAL"
    assert "entry" not in data
    assert "stop_loss" not in data


def test_all_published_fields_are_frozen(frames):
    frame = frames[0]
    for obj in (frame, frame.decision, frame.decision.eligibility, frame.settings):
        for field in fields(obj):
            with pytest.raises(FrozenInstanceError):
                setattr(obj, field.name, getattr(obj, field.name))
    assert {item.value for item in EligibilityStatus} == {"ELIGIBLE", "NOT_ELIGIBLE"}
    assert {item.value for item in MarketBias} == {"LONG_BIAS", "SHORT_BIAS", "NEUTRAL"}


def test_no_trade_probability_or_rank_fields():
    forbidden = {
        "probability",
        "confidence",
        "weight",
        "rank",
        "signal",
        "entry",
        "stop_loss",
        "take_profit",
        "position_size",
        "target_signal_count",
        "win_rate",
        "expected_value",
        "buy",
        "sell",
    }
    for cls in (
        SignalEligibilityConfig,
        SignalEligibility,
        EligibilityDecision,
        EligibilitySnapshot,
    ):
        assert not forbidden.intersection(field.name for field in fields(cls))


def test_upstream_score_objects_and_ids_are_preserved():
    raw = sqs_run()
    frames = analyze_signal_eligibility(raw)
    for frame, original in zip(frames, raw, strict=True):
        assert frame.upstream is original
        assert frame.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert frame.upstream.score is original.score


def test_changing_only_the_sqs_threshold_renames_eligibility_ids_not_halal_ids():
    raw = halal_run()
    default = analyze_signal_eligibility(analyze_setup_quality(raw))
    lowered = analyze_signal_eligibility(analyze_setup_quality(raw, SetupQualityConfig(0)))
    for left, right, original in zip(default, lowered, raw, strict=True):
        assert left.upstream.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert right.upstream.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert left.provenance.evidence_id != right.provenance.evidence_id
        assert left.eligible is False
        assert right.eligible is True


def test_snapshot_rejects_mismatched_decision_or_dependencies(frames):
    other = run()[4]
    with pytest.raises(AnalysisInputError):
        replace(frames[0], decision=other.decision)
    with pytest.raises(AnalysisInputError):
        replace(frames[0], provenance=replace(frames[0].provenance, dependencies=()))


def test_evidence_references_are_not_from_the_future(frames):
    for frame in frames:
        cutoff = frame.provenance.available_at
        for reference in frame.decision.evidence:
            assert reference.available_at <= cutoff
            assert reference.series == frame.provenance.series


def test_package_source_does_not_fetch_optimize_or_emit_orders():
    root = Path(__file__).resolve().parents[2] / "src/smcsignal/analysis/signal_eligibility"
    text = "".join(path.read_text() for path in sorted(root.glob("*.py")))
    for needle in (
        "urllib",
        "http.client",
        "requests",
        "sklearn",
        "numpy",
        "telegram",
        "BUY",
        "SELL",
        "win_rate",
    ):
        assert needle not in text
