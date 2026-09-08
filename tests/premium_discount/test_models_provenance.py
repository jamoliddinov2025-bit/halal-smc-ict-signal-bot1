import json
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from smcsignal.analysis import AnalysisInputError, EvidenceReference, ProvenancedEvidence
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.premium_discount import (
    DealingRange,
    Equilibrium,
    PDArrayContext,
    PDClassification,
    PDConfig,
    PDContextReference,
    PDSnapshot,
)
from tests.order_blocks.helpers import bullish as block_candles
from tests.premium_discount.helpers import analyzer, golden, run


@pytest.fixture
def frames():
    return run(block_candles())


def records(frames):
    seen = set()
    for frame in frames:
        ob = frame.upstream
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
            frame.dealing_range,
            frame.equilibrium,
            *frame.arrays,
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
    assert any(isinstance(r, DealingRange) for r in archive.values())
    assert any(isinstance(r, PDArrayContext) for r in archive.values())


def test_new_models_satisfy_shared_provenance_protocol(frames):
    def reference(record: ProvenancedEvidence):
        return record.provenance.as_reference()

    for record in records(frames):
        assert reference(record).series == frames[0].provenance.series


def test_configuration_artifacts_resolve_and_range_artifact_is_tolerance_independent(frames):
    detector = analyzer()
    actual = tuple(detector.update(f.upstream) for f in frames)
    main = detector.configuration_artifact
    ranges = detector.range_configuration_artifact
    assert json.loads(main)["settings"]["equilibrium_half_width_fraction"] == "0e0"
    assert json.loads(ranges)["methodology"] == "latest-confirmed-opposing-pair-v1"
    for frame in actual:
        assert frame.provenance.configuration_hash == sha256(main).hexdigest()
        if frame.dealing_range is not None:
            assert frame.dealing_range.provenance.configuration_hash == sha256(ranges).hexdigest()
            assert frame.equilibrium.provenance.configuration_hash == sha256(main).hexdigest()
        assert all(
            a.provenance.configuration_hash == sha256(main).hexdigest() for a in frame.arrays
        )


def test_full_serialization_retains_raw_anchors_range_band_and_subject_identity(frames):
    frame = frames[6]
    data = json.loads(evidence_json(frame))
    assert data["classification"] == "OUTSIDE_RANGE"
    assert Decimal(data["dealing_range"]["lower_boundary"]) == 13
    assert Decimal(data["dealing_range"]["upper_boundary"]) == 19
    assert Decimal(data["equilibrium"]["midpoint"]) == 16
    assert data["dealing_range"]["low_swing"]["swing"]["pivot_index"] == 4
    assert data["dealing_range"]["high_swing"]["swing"]["pivot_index"] == 3
    assert data["context"]["range_reference"]["evidence_id"] == frame.dealing_range.range_id
    for raw, array in zip(data["arrays"], frame.arrays, strict=True):
        assert raw["source_reference"]["evidence_id"] == array.subject.provenance.evidence_id
        assert raw["provenance"]["input_prefix_hash"] == frame.provenance.input_prefix_hash
        assert Decimal(raw["evaluated_price"]) == array.evaluated_price
        assert raw["lower_classification"] == array.lower_classification.value
        assert raw["upper_classification"] == array.upper_classification.value


def test_all_published_fields_are_frozen(frames):
    objects = []
    for frame in frames:
        objects.extend((frame, *frame.arrays))
        if frame.dealing_range is not None:
            objects.extend((frame.dealing_range, frame.equilibrium, frame.context))
    for obj in objects:
        for field in fields(obj):
            with pytest.raises(FrozenInstanceError):
                setattr(obj, field.name, getattr(obj, field.name))
    assert {c.value for c in PDClassification} == {
        "PREMIUM",
        "DISCOUNT",
        "EQUILIBRIUM",
        "OUTSIDE_RANGE",
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
    for cls in (
        DealingRange,
        Equilibrium,
        PDArrayContext,
        PDContextReference,
        PDSnapshot,
        PDConfig,
    ):
        assert not forbidden.intersection(field.name for field in fields(cls))


@pytest.mark.parametrize(
    "field,value",
    [
        ("low_swing", None),
        ("high_swing", None),
        ("context", None),
        ("price_unit", ""),
        ("provenance", None),
    ],
)
def test_invalid_range_fields_fail(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[3].dealing_range, **{field: value})


def test_wrong_kind_or_backdated_confirmation_context_is_rejected(frames):
    r = frames[3].dealing_range
    with pytest.raises(AnalysisInputError):
        replace(r, low_swing=r.high_swing)
    with pytest.raises(AnalysisInputError):
        replace(r, context=frames[2].upstream.upstream.upstream.liquidity.context)


def test_equilibrium_must_match_range_sources_and_actual_configuration(frames):
    eq = frames[3].equilibrium
    for changes in (
        {"dealing_range": None},
        {"settings": {}},
        {"provenance": None},
        {"provenance": replace(eq.provenance, dependencies=())},
    ):
        with pytest.raises(AnalysisInputError):
            replace(eq, **changes)


def test_missing_or_false_prefix_dependencies_are_rejected(frames):
    r = frames[3].dealing_range
    for metadata in (
        replace(r.provenance, dependencies=()),
        replace(r.provenance, input_prefix_hash="0" * 64),
    ):
        with pytest.raises(AnalysisInputError):
            replace(r, provenance=metadata)
    frame = frames[6]
    with pytest.raises(AnalysisInputError):
        replace(frame, provenance=replace(frame.provenance, dependencies=()))


def test_sidecar_cannot_backdate_an_ob_to_candidate_time_or_change_target_version(frames):
    array = frames[6].arrays[-1]
    assert array.kind.value == "order_block"
    with pytest.raises(AnalysisInputError):
        replace(array, evaluation=frames[4].observation)
    with pytest.raises(AnalysisInputError):
        replace(array, provenance=replace(array.provenance, input_prefix_hash="0" * 64))


@pytest.mark.parametrize(
    "field,value",
    [("settings", None), ("upstream", None), ("arrays", []), ("arrays", ()), ("provenance", None)],
)
def test_invalid_snapshot_fields_are_rejected(frames, field, value):
    with pytest.raises(AnalysisInputError):
        replace(frames[6], **{field: value})


def test_snapshot_cannot_omit_latest_swing_retain_old_pair_or_use_future_range(frames):
    with pytest.raises(AnalysisInputError):
        replace(frames[4], latest_high=None)
    with pytest.raises(AnalysisInputError):
        replace(frames[4], dealing_range=frames[3].dealing_range, equilibrium=frames[3].equilibrium)
    with pytest.raises(AnalysisInputError):
        replace(frames[3], dealing_range=frames[4].dealing_range, equilibrium=frames[4].equilibrium)


def test_context_reference_preserves_timeframe_and_availability_for_future_htf_consumers(frames):
    base = frames[3].context
    htf = replace(base.series, timeframe="1h", dataset_id="future-htf-namespace")
    # Architecture-only references, not an HTF join or computed multi-timeframe result.
    reference = PDContextReference(
        EvidenceReference("htf-range", htf, base.available_at),
        EvidenceReference("htf-equilibrium", htf, base.available_at),
    )
    assert reference.series.timeframe == "1h"
    assert reference.available_at == base.available_at
    assert reference.series != base.series
    with pytest.raises(AnalysisInputError):
        PDContextReference(reference.range_reference, base.equilibrium_reference)
    with pytest.raises(AnalysisInputError):
        PDContextReference(
            reference.range_reference,
            replace(
                reference.equilibrium_reference,
                available_at=base.available_at - timedelta(seconds=1),
            ),
        )


def test_actual_pd_consumer_does_not_accept_foreign_timeframe_ranges(frames):
    from smcsignal.analysis.premium_discount.models import context_reference

    local = frames[3]
    shifted = tuple(
        replace(c, timestamp=c.timestamp + timedelta(minutes=45 * i))
        for i, c in enumerate(golden())
    )
    source = replace(local.provenance.series, timeframe="1h")
    higher = run(shifted, series=source)[3]
    with pytest.raises(AnalysisInputError, match="local-series"):
        context_reference(higher.dealing_range, higher.equilibrium, local.observation)
