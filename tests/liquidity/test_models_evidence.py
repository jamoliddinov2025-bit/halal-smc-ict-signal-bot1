import json
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, ProvenancedEvidence
from smcsignal.analysis.liquidity import (
    LiquidityPool,
    LiquiditySnapshot,
    ObservedCandle,
    PoolStatus,
    StructureContext,
    SweepEvent,
    SwingEvidence,
    evidence_json,
)
from tests.liquidity.helpers import analyzer, golden, run, sweeps


@pytest.fixture
def frames():
    return run(golden())


def records(frames):
    for frame in frames:
        yield from frame.confirmed_swings
        yield frame.context
        yield from frame.sweeps
        yield from frame.pool_updates


def test_all_dependencies_resolve_to_exact_previously_published_snapshots(frames):
    archive = {}
    for record in records(frames):
        meta = record.provenance
        assert meta.evidence_id not in archive
        for dependency in meta.dependencies:
            assert archive[dependency.evidence_id].provenance.as_reference() == dependency
            assert dependency.available_at <= meta.available_at
        archive[meta.evidence_id] = record
    assert len(archive) > len(frames)


def test_models_satisfy_the_approved_composition_contract(frames):
    def metadata(record: ProvenancedEvidence):
        return record.provenance

    for record in records(frames):
        assert metadata(record).series == frames[0].context.provenance.series
        assert metadata(record).producer_version == "1"


def test_configuration_hashes_resolve_to_exposed_immutable_artifacts(frames):
    engine = analyzer()
    artifacts = (engine.configuration_artifact, engine.structure_configuration_artifact)
    hashes = {sha256(data).hexdigest() for data in artifacts}
    assert len(hashes) == 2
    assert {record.provenance.configuration_hash for record in records(frames)} == hashes
    assert json.loads(engine.configuration_artifact)["liquidity"]["price_unit"] == "USDT"


def test_prefix_digest_matches_independently_framed_raw_observation_archive(frames):
    source = frames[0].context.provenance.series

    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

    def instant(value):
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def number(value):
        # Independent equivalent numeric spelling for these integer-valued fixtures.
        digits = str(int(value))
        stripped = digits.rstrip("0") if value else "0"
        return stripped + "e" + str(len(digits) - len(stripped) if value else 0)

    series_record = {f.name: getattr(source, f.name) for f in fields(source)}
    hasher = sha256(encode({"schema": "observed-ohlcv-prefix-v1", "series": series_record}))
    for index, (candle, frame) in enumerate(zip(golden(), frames, strict=True)):
        observation = {
            "candle": {
                "timestamp": instant(candle.timestamp),
                **{
                    name: number(getattr(candle, name))
                    for name in ("open", "high", "low", "close", "volume")
                },
            },
            "reference": {
                "series": series_record,
                "candle_index": index,
                "opened_at": instant(candle.timestamp),
                "closed_at": instant(candle.timestamp + timedelta(minutes=15)),
            },
            "available_at": instant(candle.timestamp + timedelta(minutes=15)),
        }
        payload = encode(observation)
        hasher.update(len(payload).to_bytes(8, "big") + payload)
        assert frame.context.provenance.input_prefix_hash == hasher.hexdigest()
        for record in (*frame.confirmed_swings, *frame.sweeps, *frame.pool_updates):
            assert record.provenance.input_prefix_hash == hasher.hexdigest()


def test_json_retains_all_raw_measurements_members_lifecycle_and_provenance(frames):
    event = sweeps(frames)[0]
    data = json.loads(evidence_json(event))
    assert data["side"] == "buy_side"
    assert data["pool"]["kind"] == "equal_highs"
    assert data["pool"]["touch_count"] == len(data["pool"]["members"]) == 2
    assert Decimal(data["extreme_price"]) == event.breach.candle.high
    assert Decimal(data["reclaim_close"]) == event.breach.candle.close
    assert Decimal(data["pool"]["lower_bound"]) == event.pool.lower_bound
    assert Decimal(data["pool"]["upper_bound"]) == event.pool.upper_bound
    assert data["pool"]["settings"]["price_unit"] == "USDT"
    assert data["pool"]["provenance"]["evidence_id"] == event.pool.provenance.evidence_id
    assert data["pool"]["members"][0]["swing"]["pivot_index"] == 1
    assert len(data["pool"]["members"][0]["window"]) == 3
    assert data["confirmed_at"] == data["breach"]["available_at"]
    assert "context_before" in data and "context" in data
    terminal = next(p for p in frames[9].pool_updates if p.pool_id == event.pool.pool_id)
    retired = json.loads(evidence_json(terminal))
    assert retired["previous_snapshot"]["evidence_id"] == event.pool.provenance.evidence_id
    assert retired["sweep_reference"]["evidence_id"] == event.provenance.evidence_id
    assert retired["transition"]["reference"]["candle_index"] == 9


def test_every_field_of_every_public_record_is_frozen(frames):
    instances = [frames[9], frames[9].context.observation, *records(frames)]
    for instance in instances:
        for field in fields(instance):
            with pytest.raises(FrozenInstanceError):
                setattr(instance, field.name, getattr(instance, field.name))


def test_no_quality_or_signal_policy_fields_exist():
    forbidden = {
        "score",
        "quality_score",
        "weight",
        "confidence",
        "rank",
        "threshold",
        "signal_count",
        "target_signal_count",
    }
    for model in (
        LiquidityPool,
        SweepEvent,
        LiquiditySnapshot,
        ObservedCandle,
        SwingEvidence,
        StructureContext,
    ):
        assert not forbidden.intersection(f.name for f in fields(model))


def test_members_retain_independently_checkable_strict_fractal_windows(frames):
    for frame in frames:
        for record in frame.confirmed_swings:
            pivot = record.pivot
            neighbors = [c.candle for c in record.window if c != pivot]
            if record.swing.kind.value == "high":
                assert all(pivot.candle.high > c.high for c in neighbors)
            else:
                assert all(pivot.candle.low < c.low for c in neighbors)
            assert record.confirmation.available_at == record.provenance.available_at
            assert record.provenance.source_candles == tuple(c.reference for c in record.window)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pool_id", ""),
        ("settings", {}),
        ("members", []),
        ("members", ()),
        ("status", "active"),
        ("context", None),
        ("provenance", None),
    ],
)
def test_invalid_pool_model_fields_are_rejected(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[2].pool_updates[0], **{field: value})


def test_wrong_side_duplicate_and_out_of_band_members_are_rejected(frames):
    first = frames[2].pool_updates[0]
    for members in (
        (first.members[0], first.members[0]),
        (*first.members, frames[3].confirmed_swings[0]),
        (*first.members, frames[10].confirmed_swings[0]),
    ):
        with pytest.raises(AnalysisInputError):
            replace(first, members=members)


def test_active_pool_cannot_claim_terminal_facts(frames):
    with pytest.raises(AnalysisInputError, match="active pool"):
        replace(frames[2].pool_updates[0], transition=frames[2].context.observation)


def test_terminal_transition_requires_prior_pool_and_actual_breach(frames):
    terminal = next(p for p in frames[9].pool_updates if p.status == PoolStatus.SWEPT)
    for changes in (
        {"previous_snapshot": None},
        {"transition": None},
        {"sweep_reference": None},
        {"previous_candle": None},
        {"transition": frames[8].context.observation},
    ):
        with pytest.raises(AnalysisInputError):
            replace(terminal, **changes)


def test_terminal_record_cannot_use_later_arriving_previous_candle(frames):
    terminal = frames[9].pool_updates[0]
    delayed = replace(
        terminal.previous_candle,
        available_at=terminal.provenance.available_at + timedelta(seconds=1),
    )
    with pytest.raises(AnalysisInputError, match="later"):
        replace(terminal, previous_candle=delayed)


def test_invalidation_reason_is_checked_against_raw_facts(frames):
    invalid = frames[5].pool_updates[0]
    with pytest.raises(AnalysisInputError, match="reason"):
        replace(invalid, invalidation_reason=None)


@pytest.mark.parametrize(
    "field", ["pool", "previous", "breach", "context", "context_before", "provenance"]
)
def test_invalid_sweep_inputs_are_rejected(frames, field):
    with pytest.raises(AnalysisInputError):
        replace(sweeps(frames)[0], **{field: None})


def test_sweep_cannot_target_terminal_state(frames):
    terminal = frames[9].pool_updates[0]
    with pytest.raises(AnalysisInputError, match="pre-breach"):
        replace(sweeps(frames)[0], pool=terminal)


def test_missing_dependencies_are_rejected(frames):
    event = sweeps(frames)[0]
    incomplete = replace(event.provenance, dependencies=())
    with pytest.raises(AnalysisInputError, match="dependencies"):
        replace(event, provenance=incomplete)


def test_snapshot_must_include_the_sweep_corresponding_to_terminal_delta(frames):
    with pytest.raises(AnalysisInputError, match="must match"):
        replace(frames[9], sweeps=())


@pytest.mark.parametrize("field", ["confirmed_swings", "pool_updates", "sweeps"])
def test_mutable_snapshot_lists_are_rejected(frames, field):
    with pytest.raises(AnalysisInputError, match="immutable"):
        replace(frames[9], **{field: []})


def test_broken_window_evidence_is_rejected(frames):
    evidence = frames[2].confirmed_swings[0]
    with pytest.raises(AnalysisInputError):
        replace(evidence, window=evidence.window[:-1])
    with pytest.raises(AnalysisInputError):
        replace(evidence, swing=replace(evidence.swing, price=Decimal(99)))


def test_identity_binds_actual_configuration_and_source_namespace(frames):
    from tests.liquidity.helpers import SERIES

    changed_config = run(golden(), bps="1")
    changed_source = run(golden(), series=replace(SERIES, dataset_id="different-origin"))
    assert frames[2].pool_updates[0].pool_id != changed_config[2].pool_updates[0].pool_id
    assert frames[2].pool_updates[0].pool_id != changed_source[2].pool_updates[0].pool_id
    # Unchanged Phase 3 producers are not unnecessarily re-identified by liquidity settings.
    assert tuple(f.context for f in frames) == tuple(f.context for f in changed_config)
    assert (
        frames[0].context.provenance.evidence_id != changed_source[0].context.provenance.evidence_id
    )


def test_changed_past_volume_changes_provenance_even_when_price_events_match(frames):
    candles = golden()
    revised = run((replace(candles[0], volume=Decimal(5)), *candles[1:]))
    assert [(e.side, e.extreme_price, e.reclaim_close) for e in sweeps(revised)] == [
        (e.side, e.extreme_price, e.reclaim_close) for e in sweeps(frames)
    ]
    assert {e.sweep_id for e in sweeps(revised)}.isdisjoint(e.sweep_id for e in sweeps(frames))
