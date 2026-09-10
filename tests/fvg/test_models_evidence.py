import json
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.fvg import FVGConfig, FVGEvent, FVGSnapshot
from smcsignal.analysis.liquidity import evidence_json
from tests.fvg.helpers import analyzer, bullish, events, run
from tests.fvg.test_sweep_relationship import after_sell


@pytest.fixture
def frames():
    return run(after_sell())


def produced(frames):
    for frame in frames:
        displacement = frame.upstream
        liquidity = displacement.liquidity
        yield from liquidity.confirmed_swings
        yield liquidity.context
        yield from liquidity.sweeps
        yield from liquidity.pool_updates
        if displacement.atr_current is not None:
            yield displacement.atr_current
        yield from displacement.events
        yield displacement
        yield from frame.events
        yield frame


def test_full_dependency_graph_resolves_in_order_without_future_or_cyclic_links(frames):
    archive = {}
    for record in produced(frames):
        meta = record.provenance
        assert meta.evidence_id not in archive
        for reference in meta.dependencies:
            assert archive[reference.evidence_id].provenance.as_reference() == reference
            assert reference.available_at <= meta.available_at
        archive[meta.evidence_id] = record
    assert events(frames)


def test_source_prefix_and_exact_three_candle_dependencies_are_reused(frames):
    for frame in frames:
        assert frame.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash
        for event in frame.events:
            assert event.provenance.source_candles == (
                event.c1.reference,
                event.c2.reference,
                event.c3.reference,
            )
            assert event.provenance.input_prefix_hash == frame.upstream.provenance.input_prefix_hash
            assert all(
                f.provenance.as_reference() in event.provenance.dependencies for f in event.window
            )
            assert all(c.available_at <= event.available_at for c in (event.c1, event.c2, event.c3))


def test_configuration_artifact_binds_unit_and_exact_settings():
    engine = analyzer("0.5")
    assert engine.configuration_artifact is None
    source = run(bullish())
    actual = tuple(engine.update(f.upstream) for f in source)
    artifact = engine.configuration_artifact
    assert isinstance(artifact, bytes)
    payload = json.loads(artifact)
    assert payload["methodology"] == "fvg-v1" and payload["price_unit"] == "USDT"
    assert Decimal(payload["settings"]["min_gap_size"]) == Decimal("0.5")
    assert all(f.provenance.configuration_hash == sha256(artifact).hexdigest() for f in actual)
    assert all(
        e.provenance.configuration_hash == sha256(artifact).hexdigest() for e in events(actual)
    )


def test_immutable_models_satisfy_existing_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).series == frame.upstream.provenance.series
        for event in frame.events:
            assert reference(event).available_at == frame.upstream.provenance.available_at


def test_json_preserves_geometry_ohlc_candle_refs_configuration_and_relationships(frames):
    event = frames[13].events[0]
    data = json.loads(evidence_json(event))
    assert data["symbol"] == "BTCUSDT" and data["timeframe"] == "15m"
    assert data["price_unit"] == "USDT" and data["direction"] == "bullish"
    assert data["event_id"] == data["provenance"]["evidence_id"] == event.event_id
    assert data["creation_index"] == data["detection_index"] == 13
    assert data["threshold_version"] == "fvg-v1"
    assert Decimal(data["lower_boundary"]) == 13 and Decimal(data["upper_boundary"]) == 21
    assert Decimal(data["gap_size"]) == 8
    for name, observation in (("c1", event.c1), ("c2", event.c2), ("c3", event.c3)):
        assert data[name]["reference"]["candle_index"] == observation.reference.candle_index
        for field in ("open", "high", "low", "close", "volume"):
            assert Decimal(data[name]["candle"][field]) == getattr(observation.candle, field)
    assert data["timestamp"] == data["c3"]["reference"]["opened_at"]
    assert data["available_at"] == data["c3"]["available_at"]
    assert data["available_at"] != data["timestamp"]
    assert data["displacement_reference"]["evidence_id"] == event.associated_displacement.event_id
    assert (
        data["preceding_sweep_references"][0]["evidence_id"] == event.preceding_sweeps[0].sweep_id
    )


def test_every_field_is_frozen_and_associations_are_original_records(frames):
    for frame in frames:
        for record in (frame, *frame.events):
            for field in fields(record):
                with pytest.raises(FrozenInstanceError):
                    setattr(record, field.name, getattr(record, field.name))
    event = frames[13].events[0]
    assert event.window[1] is frames[12].upstream
    assert event.associated_displacement is frames[12].upstream.events[0]
    assert event.preceding_sweeps is frames[12].upstream.preceding_sweeps


def test_no_quality_trade_or_unimplemented_lifecycle_fields():
    forbidden = {
        "score",
        "quality_score",
        "probability",
        "confidence",
        "signal",
        "entry",
        "stop_loss",
        "target",
        "status",
        "fill",
        "filled_at",
        "invalidation",
        "active_gaps",
    }
    for model in (FVGConfig, FVGEvent, FVGSnapshot):
        assert not forbidden.intersection(f.name for f in fields(model))


@pytest.mark.parametrize(
    "field,value",
    [("settings", None), ("window", None), ("window", []), ("window", ()), ("provenance", None)],
)
def test_event_rejects_untyped_inputs(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[13].events[0], **{field: value})


def test_missing_duplicate_reordered_and_wrong_window_frames_are_rejected(frames):
    event = frames[13].events[0]
    for window in (
        event.window[:2],
        (event.window[0], event.window[0], event.window[2]),
        tuple(reversed(event.window)),
    ):
        with pytest.raises(AnalysisInputError):
            replace(event, window=window)


def test_event_cannot_bypass_minimum_or_displacement_filter():
    event = run(bullish())[2].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event, settings=FVGConfig(Decimal("1.0001")))
    with pytest.raises(AnalysisInputError):
        replace(event, settings=FVGConfig(require_displacement=True))


def test_missing_or_mismatched_source_and_context_provenance_is_rejected(frames):
    event = frames[13].events[0]
    for metadata in (
        replace(event.provenance, dependencies=()),
        replace(event.provenance, source_candles=(event.c3.reference,)),
        replace(event.provenance, input_prefix_hash="0" * 64),
    ):
        with pytest.raises(AnalysisInputError):
            replace(event, provenance=metadata)


def test_open_time_cannot_masquerade_as_fvg_availability(frames):
    event = frames[13].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event.provenance, available_at=event.c3.reference.opened_at)


@pytest.mark.parametrize(
    "field,value", [("settings", {}), ("window", []), ("events", []), ("provenance", None)]
)
def test_snapshot_rejects_untyped_inputs(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[13], **{field: value})


def test_snapshot_requires_exactly_qualifying_creation_events():
    frame = run(bullish())[2]
    for changes in (
        {"events": ()},
        {"events": (*frame.events, *frame.events)},
        {"window": frame.window[1:]},
    ):
        with pytest.raises(AnalysisInputError):
            replace(frame, **changes)


def test_snapshot_cannot_publish_another_windows_event(frames):
    valid = frames[13]
    other = run(bullish())[2].events[0]
    with pytest.raises(AnalysisInputError):
        replace(valid, events=(other,))
