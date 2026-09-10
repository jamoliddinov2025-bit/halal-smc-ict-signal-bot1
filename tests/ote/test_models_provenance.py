import json
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.ote import (
    OTEClassification,
    OTEConfig,
    OTEObservation,
    OTESnapshot,
    OTEZone,
)
from tests.ote.helpers import analyzer, golden, run, upstream


@pytest.fixture
def frames():
    return run(golden())


def records(frames):
    seen = set()
    for frame in frames:
        pd = frame.upstream
        ob = pd.upstream
        fvg = ob.upstream
        displacement = fvg.upstream
        liquidity = displacement.liquidity
        old = (
            *liquidity.confirmed_swings,
            liquidity.context,
            *liquidity.sweeps,
            *liquidity.pool_updates,
        )
        for record in (
            *old,
            *((displacement.atr_current,) if displacement.atr_current is not None else ()),
            *displacement.events,
            displacement,
            *fvg.events,
            fvg,
            *ob.events,
            ob,
            pd.dealing_range,
            pd.equilibrium,
            *pd.arrays,
            pd,
            frame.zone,
            frame.observation,
            frame,
        ):
            if record is not None and record.provenance.evidence_id not in seen:
                seen.add(record.provenance.evidence_id)
                yield record


def test_dependency_graph_resolves_exact_original_records_without_cycles(frames):
    archive = {}
    for record in records(frames):
        metadata = record.provenance
        for reference in metadata.dependencies:
            assert archive[reference.evidence_id].provenance.as_reference() == reference
            assert reference.available_at <= metadata.available_at
        archive[metadata.evidence_id] = record
    assert any(isinstance(r, OTEZone) for r in archive.values())
    assert any(isinstance(r, OTEObservation) for r in archive.values())


def test_new_models_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for record in records(frames):
        assert reference(record).series == frames[0].provenance.series


def test_configuration_artifact_binds_ote_v1_and_inherited_units(frames):
    detector = analyzer()
    actual = tuple(detector.update(f.upstream) for f in frames)
    artifact = detector.configuration_artifact
    payload = json.loads(artifact)
    assert payload["methodology"] == "ote-v1"
    assert payload["settings"]["lower_retracement"] == "62e-2"
    assert payload["settings"]["upper_retracement"] == "79e-2"
    assert payload["price_unit"] == "USDT"
    digest = sha256(artifact).hexdigest()
    for frame in actual:
        assert frame.provenance.configuration_hash == digest
        assert frame.observation.provenance.configuration_hash == digest
        if frame.zone is not None:
            assert frame.zone.provenance.configuration_hash == digest


def test_full_serialization_retains_range_anchors_and_close_classification(frames):
    frame = frames[5]
    data = json.loads(evidence_json(frame))
    assert data["classification"] == "INSIDE_OTE"
    assert Decimal(data["zone"]["lower_boundary"]) == frame.zone.lower_boundary
    assert Decimal(data["zone"]["upper_boundary"]) == frame.zone.upper_boundary
    assert data["zone"]["range_id"] == frame.upstream.dealing_range.range_id
    assert data["observation"]["zone_id"] == frame.zone.zone_id
    assert Decimal(data["observation"]["evaluated_price"]) == frame.evaluated_price


def test_all_published_fields_are_frozen(frames):
    objects = [frames[5], frames[5].zone, frames[5].observation, frames[0]]
    for obj in objects:
        for field in fields(obj):
            with pytest.raises(FrozenInstanceError):
                setattr(obj, field.name, getattr(obj, field.name))
    assert {c.value for c in OTEClassification} == {
        "INSIDE_OTE",
        "BELOW_OTE",
        "ABOVE_OTE",
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
    for cls in (OTEZone, OTEObservation, OTESnapshot, OTEConfig):
        assert not forbidden.intersection(field.name for field in fields(cls))


def test_upstream_ids_are_unchanged_by_ote_overlay(frames):
    raw = upstream(golden())
    for frame, original in zip(frames, raw, strict=True):
        assert frame.upstream.provenance.evidence_id == original.provenance.evidence_id
        if original.dealing_range is not None:
            assert frame.zone.range_id == original.dealing_range.range_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("dealing_range", None),
        ("settings", None),
        ("provenance", None),
    ],
)
def test_invalid_zone_fields_fail(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[4].zone, **{field: value})


def test_observation_rejects_a_zone_that_was_not_known_at_open(frames):
    with pytest.raises(AnalysisInputError):
        replace(frames[4].observation, zone=frames[4].zone)
    with pytest.raises(AnalysisInputError):
        replace(frames[5].observation, evaluation=frames[4].upstream.observation)


def test_snapshot_cannot_classify_with_an_unknown_zone_or_omit_the_observation(frames):
    with pytest.raises(AnalysisInputError):
        replace(frames[5], zone=None)
    with pytest.raises(AnalysisInputError):
        replace(frames[0], zone=frames[4].zone)
    with pytest.raises(AnalysisInputError):
        replace(frames[4], observation=frames[5].observation)


def test_missing_prefix_dependencies_are_rejected(frames):
    zone = frames[4].zone
    with pytest.raises(AnalysisInputError):
        replace(zone, provenance=replace(zone.provenance, dependencies=()))
    frame = frames[5]
    with pytest.raises(AnalysisInputError):
        replace(frame, provenance=replace(frame.provenance, dependencies=()))


def test_retracement_setting_changes_ote_ids_not_upstream_or_range_ids():
    default = run(golden())
    custom = run(golden(), OTEConfig(Decimal("0.5"), Decimal("0.8")))
    for a, b in zip(default, custom, strict=True):
        assert a.upstream == b.upstream
        if a.zone is not None:
            assert a.zone.range_id == b.zone.range_id
            assert a.zone.zone_id != b.zone.zone_id
            assert a.observation.observation_id != b.observation.observation_id
