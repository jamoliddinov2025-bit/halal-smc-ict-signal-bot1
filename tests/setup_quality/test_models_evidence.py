import json
from dataclasses import FrozenInstanceError, fields, replace
from hashlib import sha256
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.setup_quality import (
    WEIGHTS,
    ScoreBreakdown,
    ScoreComponent,
    ScoreSnapshot,
    SetupQualityConfig,
    SetupQualityScore,
    analyze_setup_quality,
)
from tests.halal_filter.helpers import run as halal_run
from tests.setup_quality.helpers import analyzer, run


@pytest.fixture
def frames():
    return run()


def test_new_models_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).series == frames[0].provenance.series
        assert frame.upstream.provenance.as_reference() in frame.provenance.dependencies
        assert frame.score.provenance.as_reference() in frame.provenance.dependencies
        assert frame.score.provenance.source_candles == frame.provenance.source_candles
        assert frame.provenance.producer == "sqs-frame"
        assert frame.score.provenance.producer == "sqs-score"
        assert frame.score.provenance.as_reference() not in frame.score.provenance.dependencies


def test_configuration_artifact_binds_integer_weights_and_no_signal_policy(frames):
    detector = analyzer()
    actual = tuple(detector.update(frame) for frame in halal_run())
    payload = json.loads(detector.configuration_artifact)
    assert payload["methodology"] == "sqs-v1"
    assert payload["gate"] == "non_halal_forces_zero"
    assert payload["missing"] == "zero_points"
    assert payload["signal"] is False
    assert payload["probability"] is False
    assert payload["optimization"] is False
    assert payload["weights"] == {
        component.value: WEIGHTS[component] for component in ScoreComponent
    }
    digest = sha256(detector.configuration_artifact).hexdigest()
    for frame in actual:
        assert frame.provenance.configuration_hash == digest
        assert frame.score.provenance.configuration_hash == digest
        assert frame.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash
        assert (
            frame.score.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash
        )


def test_full_serialization_retains_integer_total_and_breakdown(frames):
    data = json.loads(evidence_json(frames[4]))
    assert data["total"] == 25
    assert data["threshold_passed"] is False
    assert data["eligible"] is True
    assert data["score"]["breakdown"]["halal"] == 10
    assert data["score"]["breakdown"]["mtf"] == 15
    assert data["score"]["breakdown"]["mss"] == 0
    assert "probability" not in data
    assert "signal" not in data
    assert "entry" not in data


def test_all_published_fields_are_frozen(frames):
    frame = frames[0]
    for obj in (frame, frame.score, frame.score.breakdown, frame.settings):
        for field in fields(obj):
            with pytest.raises(FrozenInstanceError):
                setattr(obj, field.name, getattr(obj, field.name))
    assert {item.value for item in ScoreComponent} == {
        "halal",
        "mtf",
        "mss",
        "displacement",
        "liquidity_sweep",
        "fvg",
        "order_block",
        "breaker_block",
        "mitigation_block",
        "premium_discount",
        "ote",
    }


def test_no_signal_risk_or_probability_fields():
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
    }
    for cls in (SetupQualityConfig, ScoreBreakdown, SetupQualityScore, ScoreSnapshot):
        assert not forbidden.intersection(field.name for field in fields(cls))


def test_upstream_halal_objects_and_ids_are_preserved():
    raw = halal_run()
    frames = analyze_setup_quality(raw)
    for frame, original in zip(frames, raw, strict=True):
        assert frame.upstream is original
        assert frame.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert frame.upstream.decision is original.decision


def test_changing_only_the_threshold_renames_sqs_ids_not_upstream_ids():
    raw = halal_run()
    default = analyze_setup_quality(raw)
    lowered = analyze_setup_quality(raw, SetupQualityConfig(0))
    for left, right, original in zip(default, lowered, raw, strict=True):
        assert left.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert right.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert left.provenance.evidence_id != right.provenance.evidence_id
        assert left.score.provenance.evidence_id != right.score.provenance.evidence_id
        assert left.threshold_passed is False
        assert right.threshold_passed is True


def test_breakdown_rejects_points_above_component_weight():
    with pytest.raises(AnalysisInputError):
        ScoreBreakdown(11, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, tuple("x" for _ in ScoreComponent))
    with pytest.raises(AnalysisInputError):
        ScoreBreakdown(10, 16, 0, 0, 0, 0, 0, 0, 0, 0, 0, tuple("x" for _ in ScoreComponent))
    with pytest.raises(AnalysisInputError):
        ScoreBreakdown(10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ("only_one",))


def test_ineligible_score_cannot_carry_positive_points(frames):
    with pytest.raises(AnalysisInputError):
        replace(frames[0].score, eligible=False)


def test_snapshot_rejects_mismatched_score_or_dependencies(frames):
    other = run()[4]
    with pytest.raises(AnalysisInputError):
        replace(frames[0], score=other.score)
    with pytest.raises(AnalysisInputError):
        replace(frames[0], provenance=replace(frames[0].provenance, dependencies=()))


def test_package_source_does_not_fetch_optimize_or_emit_orders():
    root = Path(__file__).resolve().parents[2] / "src/smcsignal/analysis/setup_quality"
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
