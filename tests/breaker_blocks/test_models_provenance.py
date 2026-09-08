import json
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.breaker_blocks import (
    BreakerBlock,
    BreakerBlockConfig,
    BreakerEvidence,
    BreakerSnapshot,
)
from smcsignal.analysis.liquidity import evidence_json
from tests.analysis.helpers import bar
from tests.breaker_blocks.helpers import DISPLACEMENT_ONLY, analyzer, base, events, run
from tests.order_blocks.helpers import simple


@pytest.fixture
def frames():
    return run(base())


def produced(frames):
    seen = set()
    for frame in frames:
        mss = frame.upstream
        pd = mss.upstream
        ob = pd.upstream
        fvg = ob.upstream
        displacement = fvg.upstream
        liquidity = displacement.liquidity
        records = (
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
            *mss.evidence,
            *mss.events,
            mss,
            *frame.evidence,
            *frame.events,
            frame,
        )
        for item in records:
            if item is not None and item.provenance.evidence_id not in seen:
                seen.add(item.provenance.evidence_id)
                yield item


def test_all_dependencies_resolve_without_future_or_circular_references(frames):
    archive = {}
    for record in produced(frames):
        metadata = record.provenance
        for ref in metadata.dependencies:
            assert archive[ref.evidence_id].provenance.as_reference() == ref
            assert ref.available_at <= metadata.available_at
        archive[metadata.evidence_id] = record
    assert events(frames)


def test_current_prefix_and_configuration_artifact_are_exact(frames):
    engine = analyzer()
    for frame in frames:
        engine.update(frame.upstream)
    artifact = engine.configuration_artifact
    config_hash = sha256(artifact).hexdigest()
    assert json.loads(artifact)["methodology"] == "breaker-v1"
    assert json.loads(artifact)["selection"] == "all_in_original_publication_order"
    for frame in frames:
        for record in (*frame.evidence, *frame.events, frame):
            assert record.provenance.configuration_hash == config_hash
            assert (
                record.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash
            )


def test_record_types_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).available_at == frame.upstream.provenance.available_at
        for block in frame.events:
            assert reference(block.evidence) in block.provenance.dependencies
            assert reference(block).available_at == reference(frame).available_at


def test_serialization_retains_original_candidate_ob_invalidation_and_confirmation_times(frames):
    event = frames[8].events[0]
    data = json.loads(evidence_json(event))
    assert data["event_id"] == event.event_id
    assert (
        data["symbol"] == "BTCUSDT"
        and data["timeframe"] == "15m"
        and data["direction"] == "bearish"
    )
    assert data["original_candidate_index"] == 4
    assert data["original_ob_confirmation_index"] == 6
    assert (
        data["invalidation_index"]
        == data["confirmation_index"]
        == data["mss_confirmation_index"]
        == 8
    )
    assert (
        data["original_candidate_timestamp"]
        != data["original_ob_available_at"]
        != data["available_at"]
    )
    assert data["invalidation_timestamp"] != data["invalidation_available_at"]
    assert data["available_at"] == data["displacement_available_at"] == data["mss_available_at"]
    assert Decimal(data["lower_boundary"]) == Decimal(data["original_lower_boundary"]) == 13
    assert Decimal(data["upper_boundary"]) == Decimal(data["original_upper_boundary"]) == 15
    proof = data["evidence"]
    assert proof["original_ob_reference"]["evidence_id"] == event.original_ob_id
    assert proof["displacement_reference"]["evidence_id"] == event.evidence.displacement.event_id
    assert proof["mss_reference"]["evidence_id"] == event.evidence.mss.event_id
    assert (
        proof["structure_reference"]["evidence_id"]
        == event.evidence.structure_context.provenance.evidence_id
    )
    assert proof["rejection_reason"] is None and proof["qualified"] is True
    assert (
        proof["pd_reference"]["evidence_id"]
        == event.evidence.current.upstream.provenance.evidence_id
    )


def test_all_public_evidence_and_formation_fields_are_frozen(frames):
    for frame in frames:
        for record in (*frame.evidence, *frame.events, frame):
            for field in fields(record):
                with pytest.raises(FrozenInstanceError):
                    setattr(record, field.name, getattr(record, field.name))


def test_no_signal_score_entry_retest_or_manager_api_fields():
    forbidden = {
        "score",
        "quality_score",
        "probability",
        "confidence",
        "signal",
        "entry",
        "stop_loss",
        "take_profit",
        "position_size",
        "risk",
        "retest",
        "mitigation",
        "active_zones",
        "win_rate",
        "target_signal_count",
    }
    for cls in (BreakerBlockConfig, BreakerBlock, BreakerEvidence, BreakerSnapshot):
        assert not forbidden.intersection(f.name for f in fields(cls))
    assert not hasattr(analyzer(), "active_zones")


@pytest.mark.parametrize(
    "field,value",
    [
        ("settings", {}),
        ("original_ob", None),
        ("previous", None),
        ("current", None),
        ("provenance", None),
    ],
)
def test_invalid_evidence_fields_fail(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[8].evidence[0], **{field: value})


def test_no_wick_only_or_future_original_can_be_fabricated_as_evidence(frames):
    proof = frames[8].evidence[0]
    with pytest.raises(AnalysisInputError):
        replace(proof, previous=frames[6].upstream, current=frames[7].upstream)
    future_ob = frames[13].upstream.upstream.upstream.events[0]
    with pytest.raises(AnalysisInputError):
        replace(proof, original_ob=future_ob)


def test_missing_dependencies_and_false_prefix_are_rejected(frames):
    proof = frames[8].evidence[0]
    for meta in (
        replace(proof.provenance, dependencies=()),
        replace(proof.provenance, input_prefix_hash="0" * 64),
        replace(proof.provenance, source_candles=(proof.invalidating_candle.reference,)),
    ):
        with pytest.raises(AnalysisInputError):
            replace(proof, provenance=meta)


def test_old_candidate_or_ob_availability_cannot_be_claimed_as_breaker_time(frames):
    proof = frames[8].evidence[0]
    with pytest.raises(AnalysisInputError):
        replace(proof.provenance, available_at=proof.original_ob.candidate_available_at)
    with pytest.raises(AnalysisInputError):
        replace(proof.provenance, available_at=proof.original_ob.available_at)


def test_rejected_first_violation_cannot_be_cast_as_a_qualified_breaker():
    candles = (*simple(), bar(5, 81, opening=105, high=106, low=80))
    frame = run(candles, order_blocks=DISPLACEMENT_ONLY)[5]
    proof = frame.evidence[0]
    assert not proof.qualified and proof.mss is None
    with pytest.raises(AnalysisInputError, match="qualified"):
        BreakerBlock(proof, proof.provenance)


@pytest.mark.parametrize("field,value", [("evidence", None), ("provenance", None)])
def test_invalid_block_fields_fail(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[8].events[0], **{field: value})


def test_block_provenance_must_match_exact_evidence(frames):
    block = frames[8].events[0]
    for meta in (
        replace(block.provenance, dependencies=()),
        replace(block.provenance, input_prefix_hash="0" * 64),
        replace(block.provenance, configuration_hash="0" * 64),
    ):
        with pytest.raises(AnalysisInputError):
            replace(block, provenance=meta)


@pytest.mark.parametrize(
    "field,value",
    [
        ("settings", None),
        ("upstream", None),
        ("previous", None),
        ("evidence", []),
        ("events", []),
        ("events", ()),
        ("provenance", None),
    ],
)
def test_invalid_snapshot_fields_fail(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[8], **{field: value})


def test_snapshot_cannot_duplicate_or_mix_first_violation_records(frames):
    frame = frames[8]
    with pytest.raises(AnalysisInputError):
        replace(frame, evidence=(*frame.evidence, *frame.evidence))
    with pytest.raises(AnalysisInputError):
        replace(frame, events=(*frame.events, *frame.events))
    with pytest.raises(AnalysisInputError):
        replace(frames[9], evidence=frame.evidence, events=frame.events)
