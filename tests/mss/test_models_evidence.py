import json
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.mss import MSSConfig, MSSDirection, MSSEvent, MSSEvidence, MSSSnapshot
from tests.mss.helpers import analyzer, base, events, run, upstream


@pytest.fixture
def frames():
    return run(base())


def records(frames):
    seen = set()
    for frame in frames:
        pd = frame.upstream
        ob = pd.upstream
        fvg = ob.upstream
        displacement = fvg.upstream
        liquidity = displacement.liquidity
        items = (
            *liquidity.confirmed_swings,
            liquidity.context,
            *liquidity.sweeps,
            *liquidity.pool_updates,
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
            *frame.evidence,
            *frame.events,
            frame,
        )
        for record in items:
            if record is not None and record.provenance.evidence_id not in seen:
                seen.add(record.provenance.evidence_id)
                yield record


def test_complete_graph_resolves_to_existing_causal_records_without_cycles(frames):
    archive = {}
    for record in records(frames):
        meta = record.provenance
        for ref in meta.dependencies:
            assert archive[ref.evidence_id].provenance.as_reference() == ref
            assert ref.available_at <= meta.available_at
        archive[meta.evidence_id] = record
    assert events(frames)


def test_all_three_mss_record_models_carry_the_approved_provenance_contract(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).available_at == frame.upstream.observation.available_at
        for proof, event in zip(frame.evidence, frame.events, strict=True):
            assert event.evidence is proof
            assert reference(proof) in event.provenance.dependencies
            assert (
                reference(proof).available_at
                == reference(event).available_at
                == reference(frame).available_at
            )
    assert {value.value for value in MSSDirection} == {"bullish", "bearish"}


def test_prefix_and_configuration_hashes_resolve_to_actual_input_and_artifact(frames):
    engine = analyzer()
    for frame in frames:
        engine.update(frame.upstream)
    artifact = engine.configuration_artifact
    assert json.loads(artifact)["settings"] == {
        "enable_displacement_requirement": True,
        "enable_structure_requirement": True,
    }
    expected = sha256(artifact).hexdigest()
    for frame in frames:
        for record in (*frame.evidence, *frame.events, frame):
            assert record.provenance.configuration_hash == expected
            assert (
                record.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash
            )
            assert record.provenance.producer_version == "1"


def test_original_related_objects_and_ids_are_not_rewritten_by_mss():
    source = upstream(base())
    originals = {}
    for frame in source:
        parent = frame.upstream.upstream.upstream
        for item in (
            *parent.liquidity.pool_updates,
            *parent.liquidity.sweeps,
            *parent.events,
            *frame.upstream.upstream.events,
            *frame.upstream.events,
            *frame.arrays,
            frame,
        ):
            originals[item.provenance.evidence_id] = sha256(
                evidence_json(item).encode()
            ).hexdigest()
    engine = analyzer()
    results = tuple(engine.update(f) for f in source)
    for event in events(results):
        proof = event.evidence
        related = (
            *proof.broken_level_pools,
            *proof.swept_pools,
            *proof.preceding_sweeps,
            proof.displacement,
            *proof.concurrent_fvgs,
            *proof.displacement_order_blocks,
            *proof.pd_array_contexts,
            proof.current,
        )
        for item in related:
            assert (
                originals[item.provenance.evidence_id]
                == sha256(evidence_json(item).encode()).hexdigest()
            )
        assert proof.current is source[event.detection_index]
        assert proof.previous is source[event.detection_index - 1]
        assert proof.broken_level is (
            proof.previous.latest_high
            if event.direction == MSSDirection.BULLISH
            else proof.previous.latest_low
        )


def test_full_json_retains_level_prices_closed_observations_context_and_relationships(frames):
    event = frames[8].events[0]
    raw = json.loads(evidence_json(event))
    assert raw["event_id"] == event.event_id and raw["direction"] == "bearish"
    assert raw["symbol"] == "BTCUSDT" and raw["timeframe"] == "15m" and raw["price_unit"] == "USDT"
    assert raw["detection_index"] == 8
    assert raw["timestamp"] != raw["available_at"]
    proof = raw["evidence"]
    assert proof["prior_control"] == "bullish"
    assert proof["structure_break"]["kind"] == "CHoCH"
    assert Decimal(proof["level_price"]) == 13
    assert proof["broken_level"]["swing"]["pivot_index"] == 4
    assert proof["broken_level"]["swing"]["confirmed_index"] == 5
    assert proof["displacement_reference"]["evidence_id"] == event.evidence.displacement.event_id
    assert (
        proof["structure_reference"]["evidence_id"]
        == event.evidence.confirmation_structure_context.provenance.evidence_id
    )
    assert proof["pd_reference"]["evidence_id"] == event.evidence.current.provenance.evidence_id
    assert proof["pd_classification"] == event.evidence.pd_classification.value
    assert proof["concurrent_fvgs"] and proof["displacement_order_blocks"]


def test_mss_fields_and_config_are_all_immutable(frames):
    for frame in frames:
        for record in (*frame.evidence, *frame.events, frame, MSSConfig()):
            for field in fields(record):
                with pytest.raises(FrozenInstanceError):
                    setattr(record, field.name, getattr(record, field.name))


def test_no_signal_risk_scoring_or_lifecycle_fields():
    forbidden = {
        "signal",
        "entry",
        "stop",
        "target",
        "position_size",
        "risk",
        "score",
        "probability",
        "confidence",
        "win_rate",
        "rank",
        "target_signal_count",
        "status",
    }
    for model in (MSSEvent, MSSEvidence, MSSSnapshot, MSSConfig):
        assert not forbidden.intersection(field.name for field in fields(model))


@pytest.mark.parametrize(
    "field,value", [("settings", None), ("previous", None), ("current", None), ("provenance", None)]
)
def test_untyped_evidence_inputs_fail(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[8].evidence[0], **{field: value})


def test_wrong_prior_or_future_context_cannot_be_substituted(frames):
    proof = frames[8].evidence[0]
    for previous in (frames[6].upstream, frames[8].upstream, frames[9].upstream):
        with pytest.raises(AnalysisInputError):
            replace(proof, previous=previous)


def test_non_mss_upstream_frames_cannot_fabricate_evidence(frames):
    proof = frames[8].evidence[0]
    with pytest.raises(AnalysisInputError):
        replace(proof, previous=frames[5].upstream, current=frames[6].upstream)


def test_required_level_and_context_dependencies_cannot_be_omitted(frames):
    proof = frames[8].evidence[0]
    for metadata in (
        replace(proof.provenance, dependencies=()),
        replace(proof.provenance, source_candles=(proof.current.observation.reference,)),
        replace(proof.provenance, input_prefix_hash="0" * 64),
    ):
        with pytest.raises(AnalysisInputError):
            replace(proof, provenance=metadata)


def test_old_level_confirmation_time_cannot_be_mss_publication_time(frames):
    proof = frames[8].evidence[0]
    with pytest.raises(AnalysisInputError):
        replace(proof.provenance, available_at=proof.broken_level.provenance.available_at)


@pytest.mark.parametrize("field,value", [("evidence", None), ("provenance", None)])
def test_invalid_event_fields_fail(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[8].events[0], **{field: value})


def test_event_provenance_must_match_its_qualified_evidence(frames):
    event = frames[8].events[0]
    for metadata in (
        replace(event.provenance, dependencies=()),
        replace(event.provenance, input_prefix_hash="0" * 64),
        replace(event.provenance, configuration_hash="0" * 64),
    ):
        with pytest.raises(AnalysisInputError):
            replace(event, provenance=metadata)


@pytest.mark.parametrize(
    "field,value",
    [
        ("settings", {}),
        ("upstream", None),
        ("evidence", []),
        ("evidence", ()),
        ("events", []),
        ("events", ()),
        ("provenance", None),
    ],
)
def test_snapshot_cannot_omit_or_mutate_qualified_deltas(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[8], **{field: value})


def test_snapshot_cannot_duplicate_events_or_publish_another_candles_evidence(frames):
    current = frames[8]
    with pytest.raises(AnalysisInputError):
        replace(current, events=(*current.events, *current.events))
    with pytest.raises(AnalysisInputError):
        replace(frames[9], evidence=current.evidence, events=current.events)
