import json
from dataclasses import FrozenInstanceError, fields, replace
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.mtf import (
    MTFConfig,
    MTFDirection,
    MTFEvidenceKind,
    MTFEvidenceReference,
    MTFRelation,
    MTFSnapshot,
    analyze_mtf,
)
from tests.mtf.helpers import analyzer, four_hour_frames, hour_frames, primary_15m, run


@pytest.fixture
def frames():
    return run()


def test_new_models_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).series == frames[0].provenance.series
        assert frame.upstream.provenance.as_reference() in frame.provenance.dependencies


def test_configuration_artifact_binds_mtf_v1_and_open_time_availability(frames):
    detector = analyzer()
    actual = tuple(detector.update(frame) for frame in primary_15m())
    payload = json.loads(detector.configuration_artifact)
    assert payload["methodology"] == "mtf-v1"
    assert payload["availability"] == "htf_available_at_lte_primary_open"
    assert payload["confluence"] == "unweighted_label_agreement"
    digest = sha256(detector.configuration_artifact).hexdigest()
    for frame in actual:
        assert frame.provenance.configuration_hash == digest
        assert frame.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash


def test_full_serialization_retains_independent_htf_states(frames):
    data = json.loads(evidence_json(frames[4]))
    assert data["direction"] == "BULLISH"
    assert data["relations"][0]["timeframe"] == "1h"
    assert data["relations"][0]["direction"] == "BULLISH"
    assert data["relations"][1]["latest"] is None
    assert Decimal_safe(data)


def Decimal_safe(data):
    assert "score" not in data
    return True


def test_all_published_fields_are_frozen(frames):
    frame = frames[4]
    for obj in (frame, frame.relations[0], frame.evidence[0]):
        for field in fields(obj):
            with pytest.raises(FrozenInstanceError):
                setattr(obj, field.name, getattr(obj, field.name))
    assert {item.value for item in MTFDirection} == {
        "BULLISH",
        "BEARISH",
        "MIXED",
        "NEUTRAL",
        "INSUFFICIENT_CONTEXT",
    }


def test_no_signal_risk_or_score_fields():
    forbidden = {
        "score",
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
    }
    for cls in (MTFConfig, MTFSnapshot, MTFRelation, MTFEvidenceReference):
        assert not forbidden.intersection(field.name for field in fields(cls))


def test_upstream_ote_objects_and_ids_are_preserved():
    raw = primary_15m()
    frames = analyze_mtf(raw, {"1h": hour_frames(), "4h": four_hour_frames()})
    for frame, original in zip(frames, raw, strict=True):
        assert frame.upstream is original
        assert frame.upstream.provenance.evidence_id == original.provenance.evidence_id


def test_snapshot_rejects_future_htf_evidence(frames):
    noon = frames[16]
    with pytest.raises(AnalysisInputError):
        replace(frames[4], relations=noon.relations)
    with pytest.raises(AnalysisInputError):
        replace(frames[4], provenance=replace(frames[4].provenance, dependencies=()))


def test_relation_cannot_carry_a_mixed_label(frames):
    with pytest.raises(AnalysisInputError):
        replace(frames[4].relations[0], direction=MTFDirection.MIXED)


def test_evidence_kind_rank_is_stable():
    assert list(MTFEvidenceKind)[0] is MTFEvidenceKind.STRUCTURE_CONTEXT
    assert list(MTFEvidenceKind)[-1] is MTFEvidenceKind.OTE_SNAPSHOT


def test_prefix_hash_is_the_primary_consumed_prefix_not_a_future_htf_file_hash(frames):
    hourly = hour_frames()
    assert frames[0].provenance.input_prefix_hash == primary_15m()[0].provenance.input_prefix_hash
    assert frames[16].relations[1].latest is not None
    assert frames[16].relations[1].latest.provenance.series.timeframe == "4h"
    assert hourly[-1].provenance.input_prefix_hash != frames[16].provenance.input_prefix_hash
