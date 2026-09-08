import json
from dataclasses import FrozenInstanceError, fields, replace
from hashlib import sha256
from pathlib import Path

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.halal_filter import (
    AssetClassification,
    FilterMode,
    HalalDecision,
    HalalFilterConfig,
    HalalSnapshot,
    analyze_halal,
    classify_asset,
)
from smcsignal.analysis.liquidity import evidence_json
from tests.halal_filter.helpers import analyzer, mtf_for, run


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
        assert frame.decision.provenance.source_candles == ()
        assert frame.provenance.producer == "halal-frame"
        assert frame.decision.provenance.producer == "halal-decision"


def test_configuration_artifact_binds_registry_only_policy(frames):
    detector = analyzer()
    actual = tuple(detector.update(frame) for frame in mtf_for())
    payload = json.loads(detector.configuration_artifact)
    assert payload["methodology"] == "halal-filter-v1"
    assert payload["policy"] == "registry_enforcement_only"
    assert payload["unknown"] == "never_silently_halal"
    assert payload["internet"] is False
    assert payload["autonomous_rulings"] is False
    digest = sha256(detector.configuration_artifact).hexdigest()
    for frame in actual:
        assert frame.provenance.configuration_hash == digest
        assert frame.decision.provenance.configuration_hash == digest
        assert frame.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash


def test_full_serialization_retains_explicit_unknown_policy(frames):
    data = json.loads(evidence_json(frames[0]))
    assert data["classification"] == "HALAL"
    assert data["eligible"] is True
    assert data["decision"]["symbol"] == "BTCUSDT"
    assert data["decision"]["reason"] == "listed_in_allow_list"
    assert "score" not in data
    assert "signal" not in data


def test_all_published_fields_are_frozen(frames):
    frame = frames[0]
    for obj in (frame, frame.decision, frame.settings):
        for field in fields(obj):
            with pytest.raises(FrozenInstanceError):
                setattr(obj, field.name, getattr(obj, field.name))
    assert {item.value for item in AssetClassification} == {"HALAL", "HARAM", "UNKNOWN"}
    assert {item.value for item in FilterMode} == {"allow_list", "deny_list"}


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
    for cls in (HalalFilterConfig, HalalDecision, HalalSnapshot):
        assert not forbidden.intersection(field.name for field in fields(cls))


def test_upstream_mtf_objects_and_ids_are_preserved():
    raw = mtf_for()
    frames = analyze_halal(raw)
    for frame, original in zip(frames, raw, strict=True):
        assert frame.upstream is original
        assert frame.upstream.provenance.evidence_id == original.provenance.evidence_id
        assert frame.decision is frames[0].decision


def test_unknown_snapshot_is_explicit_and_ineligible():
    frames = run(mtf_for("ADAUSDT"))
    assert frames[0].classification is AssetClassification.UNKNOWN
    assert frames[0].eligible is False
    assert frames[0].decision.reason == "absent_from_allow_list"
    assert frames[0].decision.eligible is False
    assert classify_asset("ADAUSDT") is AssetClassification.UNKNOWN


def test_haram_snapshot_is_explicit_and_ineligible():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )
    frames = run(mtf_for("XYZUSDT"), config)
    assert frames[0].classification is AssetClassification.HARAM
    assert frames[0].eligible is False
    assert frames[0].decision.reason == "listed_in_deny_list"


def test_deny_list_unlisted_asset_stays_unknown():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )
    frames = run(mtf_for("BTCUSDT"), config)
    assert frames[0].classification is AssetClassification.UNKNOWN
    assert frames[0].eligible is False
    assert frames[0].decision.reason == "absent_from_deny_list"


def test_snapshot_rejects_mismatched_decision_series(frames):
    other = run(mtf_for("ETHUSDT"))[0]
    with pytest.raises(AnalysisInputError):
        replace(frames[0], decision=other.decision)
    with pytest.raises(AnalysisInputError):
        replace(frames[0], provenance=replace(frames[0].provenance, dependencies=()))


def test_decision_cannot_carry_source_candles(frames):
    with pytest.raises(AnalysisInputError):
        replace(
            frames[0].decision,
            provenance=replace(
                frames[0].decision.provenance,
                source_candles=frames[0].provenance.source_candles,
            ),
        )


def test_package_source_does_not_fetch_or_scrape():
    root = Path(__file__).resolve().parents[2] / "src/smcsignal/analysis/halal_filter"
    text = "".join(path.read_text() for path in sorted(root.glob("*.py")))
    for needle in ("urllib", "http.client", "requests", "aiohttp", "websocket", "CryptoIslam"):
        assert needle not in text
