import json
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal, localcontext
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.displacement import (
    ATRReference,
    DisplacementEvent,
    DisplacementMetrics,
    DisplacementSnapshot,
)
from smcsignal.analysis.liquidity import evidence_json
from tests.displacement.helpers import analyzer, events, run
from tests.displacement.test_sweep_context import after_sell_candles


@pytest.fixture
def frames():
    return run(after_sell_candles())


def produced(frames):
    for frame in frames:
        parent = frame.liquidity
        yield from parent.confirmed_swings
        yield parent.context
        yield from parent.sweeps
        yield from parent.pool_updates
        if frame.atr_current is not None:
            yield frame.atr_current
        yield from frame.events
        yield frame


def test_complete_dependency_graph_resolves_in_publication_order_without_cycles(frames):
    archive = {}
    for record in produced(frames):
        metadata = record.provenance
        assert metadata.evidence_id not in archive
        for dependency in metadata.dependencies:
            assert archive[dependency.evidence_id].provenance.as_reference() == dependency
            assert dependency.available_at <= metadata.available_at
        archive[metadata.evidence_id] = record
    assert events(frames)


def test_reuse_of_canonical_input_hash_does_not_fork_or_recompute_the_upstream_prefix(frames):
    for index, frame in enumerate(frames):
        assert (
            frame.provenance.input_prefix_hash
            == frame.liquidity.context.provenance.input_prefix_hash
        )
        if frame.atr_current is not None:
            assert (
                frame.atr_current.provenance.input_prefix_hash == frame.provenance.input_prefix_hash
            )
        for event in frame.events:
            assert event.provenance.input_prefix_hash == frame.provenance.input_prefix_hash
            assert (
                event.atr_reference.provenance.input_prefix_hash
                == frames[index - 1].provenance.input_prefix_hash
            )
            assert event.atr_reference.end_index == index - 1
            assert event.atr_reference.provenance.as_reference() in event.provenance.dependencies
            assert frame.atr_current.provenance.as_reference() not in event.provenance.dependencies


def test_all_configuration_hashes_resolve_to_exact_exposed_artifacts(frames):
    engine = analyzer()
    displacement_hash = sha256(engine.configuration_artifact).hexdigest()
    atr_hash = sha256(engine.atr_configuration_artifact).hexdigest()
    for frame in frames:
        assert frame.provenance.configuration_hash == displacement_hash
        if frame.atr_current is not None:
            assert frame.atr_current.provenance.configuration_hash == atr_hash
        assert all(e.provenance.configuration_hash == displacement_hash for e in frame.events)
    assert json.loads(engine.configuration_artifact)["settings"]["atr_period"] == 3
    assert json.loads(engine.atr_configuration_artifact)["methodology"] == "atr-sma-v1"


def test_models_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for frame in frames:
        assert reference(frame).available_at == frame.liquidity.context.observation.available_at
        if frame.atr_current is not None:
            assert reference(frame.atr_current).available_at == reference(frame).available_at
        for event in frame.events:
            assert reference(event).available_at == reference(frame).available_at


def test_full_json_retains_raw_ohlc_atr_window_thresholds_times_and_sweep_links(frames):
    event = frames[12].events[0]
    data = json.loads(evidence_json(event))
    assert data["event_id"] == event.event_id
    assert data["symbol"] == "BTCUSDT" and data["timeframe"] == "15m"
    assert data["start_index"] == data["end_index"] == data["detection_index"] == 12
    assert data["threshold_version"] == "displacement-v1"
    assert Decimal(data["metrics"]["body_size"]) == event.body_size
    assert Decimal(data["metrics"]["range_size"]) == event.range_size
    assert Decimal(data["metrics"]["close_location_ratio"]) == event.close_location_ratio
    reference = data["metrics"]["atr_reference"]
    assert reference["period"] == 3 and reference["end_index"] == 11
    assert len(reference["observations"]) == 4 and len(reference["true_ranges"]) == 3
    assert Decimal(reference["total_true_range"]) == event.atr_reference.total_true_range
    raw = data["metrics"]["observation"]["candle"]
    assert {name: Decimal(raw[name]) for name in ("open", "high", "low", "close")} == {
        "open": Decimal(12),
        "high": Decimal(23),
        "low": Decimal(11),
        "close": Decimal(22),
    }
    assert (
        data["preceding_sweep_references"][0]["evidence_id"]
        == frames[10].liquidity.sweeps[0].sweep_id
    )
    assert data["available_at"] == data["provenance"]["available_at"]
    assert data["timestamp"] != data["available_at"]


def test_each_published_atr_is_reconstructible_from_its_raw_window(frames):
    for frame in frames:
        reference = frame.atr_current
        if reference is None:
            continue
        with localcontext() as context:
            context.prec = 100
            ranges = tuple(
                max(
                    c.candle.high - c.candle.low,
                    abs(c.candle.high - p.candle.close),
                    abs(c.candle.low - p.candle.close),
                )
                for p, c in zip(reference.observations, reference.observations[1:], strict=False)
            )
        assert ranges == reference.true_ranges
        assert sum(ranges, Decimal(0)) == reference.total_true_range
        assert (
            tuple(c.reference for c in reference.observations)
            == reference.provenance.source_candles
        )


def test_every_published_field_is_immutable(frames):
    objects = [*produced(frames), *(f.metrics for f in frames)]
    for record in objects:
        for field in fields(record):
            with pytest.raises(FrozenInstanceError):
                setattr(record, field.name, getattr(record, field.name))


def test_no_probability_quality_or_signal_policy_fields_exist():
    forbidden = {
        "score",
        "quality_score",
        "confidence",
        "probability",
        "weight",
        "rank",
        "signal",
        "buy_signal",
        "sell_signal",
        "target_signal_count",
    }
    for model in (ATRReference, DisplacementMetrics, DisplacementEvent, DisplacementSnapshot):
        assert not forbidden.intersection(f.name for f in fields(model))


@pytest.mark.parametrize(
    "name,value",
    [
        ("period", 0),
        ("period", True),
        ("observations", []),
        ("observations", ()),
        ("context", None),
        ("provenance", None),
    ],
)
def test_bad_atr_fields_rejected(frames, name, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[3].atr_current, **{name: value})


def test_atr_requires_actual_previous_close_anchor_and_unique_consecutive_observations(frames):
    ref = frames[3].atr_current
    with pytest.raises(AnalysisInputError):
        replace(ref, observations=ref.observations[1:])
    with pytest.raises(AnalysisInputError):
        replace(ref, observations=(ref.observations[0], ref.observations[0], *ref.observations[2:]))


def test_current_or_future_atr_cannot_be_used_for_current_metrics(frames):
    frame = frames[12]
    with pytest.raises(AnalysisInputError, match="t-1"):
        replace(frame.metrics, atr_reference=frame.atr_current)
    with pytest.raises(AnalysisInputError):
        replace(frame.metrics, observation=None)


@pytest.mark.parametrize(
    "name,value",
    [
        ("settings", {}),
        ("price_unit", ""),
        ("metrics", None),
        ("context", None),
        ("preceding_sweeps", []),
        ("provenance", None),
    ],
)
def test_bad_event_fields_rejected(frames, name, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[12].events[0], **{name: value})


def test_no_qualifying_event_can_be_fabricated_from_a_doji(frames):
    event = frames[12].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event, metrics=frames[11].metrics, context=frames[11].liquidity.context)


def test_period_and_provenance_dependency_mismatches_are_rejected(frames):
    event = frames[12].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event, settings=replace(event.settings, atr_period=4))
    with pytest.raises(AnalysisInputError, match="dependencies"):
        replace(event, provenance=replace(event.provenance, dependencies=()))
    with pytest.raises(AnalysisInputError, match="prefix"):
        replace(event, provenance=replace(event.provenance, input_prefix_hash="0" * 64))


def test_expired_duplicate_context_and_wrong_series_are_rejected(frames):
    event = frames[12].events[0]
    with pytest.raises(AnalysisInputError):
        replace(event, settings=replace(event.settings, sweep_lookback_bars=1))
    with pytest.raises(AnalysisInputError):
        replace(event, preceding_sweeps=(*event.preceding_sweeps, *event.preceding_sweeps))
    sweep = event.preceding_sweeps[0]
    # A candidate cannot substitute another series for its historical evidence.
    observation = replace(
        event.observation,
        reference=replace(
            event.observation.reference,
            series=replace(sweep.provenance.series, dataset_id="other-series"),
        ),
    )
    with pytest.raises(AnalysisInputError):
        replace(event.metrics, observation=observation)


@pytest.mark.parametrize("name", ["liquidity", "settings", "metrics", "provenance"])
def test_bad_snapshot_fields_rejected(frames, name):
    with pytest.raises(AnalysisInputError):
        replace(frames[12], **{name: None})


def test_snapshot_rejects_mutable_missing_and_duplicate_events(frames):
    frame = frames[12]
    for value in ([], (), (*frame.events, *frame.events)):
        with pytest.raises(AnalysisInputError):
            replace(frame, events=value)


def test_snapshot_cannot_borrow_future_readiness_or_mislabel_current_atr(frames):
    with pytest.raises(AnalysisInputError):
        replace(frames[2], atr_current=frames[3].atr_current)
    with pytest.raises(AnalysisInputError):
        replace(frames[12], atr_current=frames[11].atr_current)
