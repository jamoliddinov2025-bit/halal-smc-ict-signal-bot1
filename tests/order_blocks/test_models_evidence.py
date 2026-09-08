import json
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.order_blocks import (
    OrderBlockConfig,
    OrderBlockEvent,
    OrderBlockSnapshot,
    StructureRequirement,
)
from tests.order_blocks.helpers import analyzer, bullish, events, run, simple


@pytest.fixture
def frames():
    return run(bullish(), require_fvg=True)


def produced(frames):
    for frame in frames:
        fvg = frame.upstream
        displacement = fvg.upstream
        liquidity = displacement.liquidity
        yield from liquidity.confirmed_swings
        yield liquidity.context
        yield from liquidity.sweeps
        yield from liquidity.pool_updates
        if displacement.atr_current is not None:
            yield displacement.atr_current
        yield from displacement.events
        yield displacement
        yield from fvg.events
        yield fvg
        yield from frame.events
        yield frame


def test_all_references_resolve_in_publication_order_without_cycles(frames):
    archive = {}
    for record in produced(frames):
        meta = record.provenance
        assert meta.evidence_id not in archive
        for reference in meta.dependencies:
            assert archive[reference.evidence_id].provenance.as_reference() == reference
            assert reference.available_at <= meta.available_at
        archive[meta.evidence_id] = record
    assert events(frames)


def test_configuration_artifact_and_shared_provenance_protocol(frames):
    detector = analyzer(require_fvg=True)
    actual = tuple(detector.update(frame.upstream) for frame in frames)
    artifact = detector.configuration_artifact
    assert isinstance(artifact, bytes)
    data = json.loads(artifact)
    assert data["methodology"] == "order-block-v1"
    assert data["settings"]["structure_requirement"] == "bos_or_choch"
    assert data["price_unit"] == "USDT"

    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in actual:
        assert reference(frame).series == frame.upstream.provenance.series
        assert frame.provenance.configuration_hash == sha256(artifact).hexdigest()
        for event in frame.events:
            assert event.provenance.configuration_hash == frame.provenance.configuration_hash
            assert reference(event).available_at == frame.provenance.available_at


def test_candidate_and_confirmation_source_provenance_is_complete(frames):
    event = frames[7].events[0]
    assert event.candidate_frame.provenance.as_reference() in event.provenance.dependencies
    assert event.displacement_reference in event.provenance.dependencies
    assert event.structure_reference in event.provenance.dependencies
    assert event.fvg_reference in event.provenance.dependencies
    assert event.publication.provenance.as_reference() in event.provenance.dependencies
    assert [r.candle_index for r in event.provenance.source_candles] == list(range(8))
    assert event.provenance.input_prefix_hash == frames[7].upstream.provenance.input_prefix_hash
    assert event.candidate_frame.provenance.input_prefix_hash != event.provenance.input_prefix_hash
    assert event.candidate_available_at < event.displacement_available_at < event.available_at


def test_complete_json_retains_zone_ohlc_classification_refs_and_each_timestamp(frames):
    event = frames[7].events[0]
    data = json.loads(evidence_json(event))
    assert data["event_id"] == data["provenance"]["evidence_id"] == event.event_id
    assert data["symbol"] == "BTCUSDT" and data["timeframe"] == "15m"
    assert data["direction"] == "bullish" and data["candidate_classification"] == "bearish"
    assert (
        data["candidate_index"] == 4
        and data["displacement_index"] == 6
        and data["confirmation_index"] == 7
    )
    assert data["candidate_distance"] == 2
    assert data["threshold_version"] == "order-block-v1"
    assert Decimal(data["zone_lower_boundary"]) == 13 and Decimal(data["zone_upper_boundary"]) == 15
    assert Decimal(data["zone_size"]) == 2
    assert {
        n: Decimal(data["candidate"]["candle"][n]) for n in ("open", "high", "low", "close")
    } == {"open": Decimal(15), "high": Decimal(15), "low": Decimal(13), "close": Decimal(14)}
    assert data["structure_event"]["kind"] == "BOS"
    assert (
        data["structure_reference"]["evidence_id"] == event.structure_context.provenance.evidence_id
    )
    assert data["displacement_reference"]["evidence_id"] == event.displacement.event_id
    assert data["fvg_reference"]["evidence_id"] == event.associated_fvg.event_id
    assert data["available_at"] == data["fvg_available_at"]
    assert data["candidate_timestamp"] != data["confirmation_timestamp"] != data["available_at"]


def test_all_public_model_fields_are_frozen_and_original_objects_are_retained(frames):
    for frame in frames:
        for record in (frame, *frame.events):
            for field in fields(record):
                with pytest.raises(FrozenInstanceError):
                    setattr(record, field.name, getattr(record, field.name))
    event = frames[7].events[0]
    assert event.candidate_frame is frames[4].upstream.upstream
    assert event.candidate is event.candidate_frame.metrics.observation
    assert event.displacement is frames[6].upstream.upstream.events[0]
    assert event.associated_fvg is frames[7].upstream.events[0]


def test_no_lifecycle_score_probability_or_entry_fields():
    forbidden = {
        "score",
        "quality_score",
        "probability",
        "win_probability",
        "signal",
        "entry",
        "stop_loss",
        "take_profit",
        "status",
        "mitigation",
        "invalidation",
        "trade_management",
        "target_signal_count",
    }
    for model in (OrderBlockConfig, OrderBlockEvent, OrderBlockSnapshot):
        assert not forbidden.intersection(f.name for f in fields(model))


@pytest.mark.parametrize(
    "name,value",
    [
        ("settings", None),
        ("candidate_window", []),
        ("candidate_window", ()),
        ("displacement_frame", None),
        ("publication", None),
        ("provenance", None),
    ],
)
def test_invalid_event_inputs_are_rejected(frames, name, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[7].events[0], **{name: value})


def test_missing_or_nonchronological_selection_window_is_rejected(frames):
    event = frames[7].events[0]
    for window in (
        event.candidate_window[1:],
        tuple(reversed(event.candidate_window)),
        (*event.candidate_window[:-1], event.candidate_window[0]),
    ):
        with pytest.raises(AnalysisInputError):
            replace(event, candidate_window=window)


def test_future_or_wrong_confirmation_cannot_be_substituted(frames):
    event = frames[7].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event, publication=frames[6].upstream)
    with pytest.raises(AnalysisInputError):
        replace(event, displacement_frame=frames[7].upstream.upstream)
    with pytest.raises(AnalysisInputError):
        replace(event, settings=replace(event.settings, require_fvg=False))


def test_wrong_structure_mode_and_source_references_are_rejected(frames):
    event = frames[7].events[0]
    with pytest.raises(AnalysisInputError):
        replace(
            event,
            settings=replace(event.settings, structure_requirement=StructureRequirement.CHOCH),
        )
    for metadata in (
        replace(event.provenance, dependencies=()),
        replace(event.provenance, source_candles=(event.provenance.source_candles[-1],)),
        replace(event.provenance, input_prefix_hash="0" * 64),
    ):
        with pytest.raises(AnalysisInputError):
            replace(event, provenance=metadata)


def test_candidate_timestamp_cannot_be_used_as_ob_availability(frames):
    event = frames[7].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event.provenance, available_at=event.candidate_timestamp)


@pytest.mark.parametrize(
    "name,value", [("settings", {}), ("upstream", None), ("events", []), ("provenance", None)]
)
def test_invalid_snapshot_inputs_are_rejected(frames, name, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[7], **{name: value})


def test_duplicate_or_wrong_frame_events_are_rejected(frames):
    frame = frames[7]
    with pytest.raises(AnalysisInputError):
        replace(frame, events=(*frame.events, *frame.events))
    with pytest.raises(AnalysisInputError):
        replace(frames[6], events=frame.events)


def test_large_range_is_raw_evidence_not_a_clipped_zone():
    candles = list(simple())
    from tests.analysis.helpers import bar

    candles[3] = bar(3, 100, opening=110, high=150, low=50)
    candles[4] = bar(4, 200, opening=100, high=205, low=99)
    (event,) = events(run(candles, structure_requirement=StructureRequirement.DISPLACEMENT_ONLY))
    assert (
        event.zone_lower_boundary == 50
        and event.zone_upper_boundary == 150
        and event.zone_size == 100
    )
