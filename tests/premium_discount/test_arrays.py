from hashlib import sha256

from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.liquidity import evidence_json
from smcsignal.analysis.order_blocks import OrderBlockConfig
from smcsignal.analysis.premium_discount import PDArrayKind, PDClassification, analyze_pd
from smcsignal.analysis.premium_discount.calculation import midpoint, published_arrays
from tests.analysis.helpers import series
from tests.liquidity.helpers import golden as liquidity_candles
from tests.order_blocks.helpers import bullish as block_candles
from tests.order_blocks.helpers import golden as block_golden
from tests.premium_discount.helpers import golden, run, upstream


def test_annotations_preserve_every_original_object_id_and_serialized_content():
    parent = upstream(block_golden(), displacement=DisplacementConfig())
    original = {
        obj.provenance.evidence_id: sha256(evidence_json(obj).encode()).hexdigest()
        for frame in parent
        for obj in published_arrays(frame)
    }
    frames = analyze_pd(parent)
    assert all(frame.upstream is raw for frame, raw in zip(frames, parent, strict=True))
    for frame in frames:
        expected = published_arrays(frame.upstream)
        assert len(frame.arrays) == len(expected)
        for annotation, subject in zip(frame.arrays, expected, strict=True):
            assert annotation.subject is subject
            assert annotation.source_reference == subject.provenance.as_reference()
            assert annotation.provenance.evidence_id != subject.provenance.evidence_id
            assert (
                original[subject.provenance.evidence_id]
                == sha256(evidence_json(subject).encode()).hexdigest()
            )
            assert annotation.evaluation is frame.observation
            assert annotation.context == frame.context


def test_all_five_pd_array_kinds_are_supported_without_new_detection():
    frames = (*run(liquidity_candles()), *run(block_candles()))
    assert {a.kind for f in frames for a in f.arrays} == set(PDArrayKind)


def test_liquidity_pool_uses_fixed_anchor_and_retains_raw_band_endpoints():
    frame = run(golden())[4]
    array = next(a for a in frame.arrays if a.kind == PDArrayKind.LIQUIDITY_POOL)
    assert array.evaluated_price == array.subject.reference_price == 16
    assert (array.lower_price, array.upper_price) == (
        array.subject.lower_bound,
        array.subject.upper_bound,
    )
    assert array.classification == PDClassification.PREMIUM
    assert frame.classification == PDClassification.DISCOUNT


def test_sweep_pd_uses_extreme_not_reclaim_close_and_no_future_array_revisions():
    frames = run(liquidity_candles())
    array = next(a for a in frames[9].arrays if a.kind == PDArrayKind.SWEEP)
    assert array.evaluated_price == array.subject.extreme_price == 17
    assert array.subject.reclaim_close == 12
    assert array.classification == PDClassification.OUTSIDE_RANGE
    assert frames[9].classification == PDClassification.DISCOUNT
    assert all(
        a.source_reference.evidence_id != array.source_reference.evidence_id
        for f in frames[10:]
        for a in f.arrays
    )


def test_displacement_close_and_ob_zone_midpoint_are_distinct_from_candle_label():
    frame = run(block_candles())[6]
    displacement = next(a for a in frame.arrays if a.kind == PDArrayKind.DISPLACEMENT)
    block = next(a for a in frame.arrays if a.kind == PDArrayKind.ORDER_BLOCK)
    assert displacement.evaluated_price == displacement.subject.observation.candle.close == 20
    assert displacement.classification == PDClassification.OUTSIDE_RANGE
    assert (
        block.evaluated_price
        == midpoint(block.subject.zone_lower_boundary, block.subject.zone_upper_boundary)
        == 14
    )
    assert block.classification == PDClassification.DISCOUNT


def test_fvg_zone_midpoint_and_endpoint_labels_reveal_straddling_not_whole_zone_claim():
    frame = run(series([10, 15, 20, 12, 14, 17]))[4]
    array = next(a for a in frame.arrays if a.kind == PDArrayKind.FVG)
    assert (array.lower_price, array.upper_price, array.evaluated_price) == (15, 19, 17)
    assert array.classification == PDClassification.PREMIUM
    assert array.lower_classification == PDClassification.DISCOUNT
    assert array.upper_classification == PDClassification.PREMIUM


def test_deferred_ob_gets_publication_time_context_not_candidate_time_context():
    frames = run(block_candles(), order_blocks=OrderBlockConfig(require_fvg=True))
    array = next(a for a in frames[7].arrays if a.kind == PDArrayKind.ORDER_BLOCK)
    assert array.subject.candidate_index == 4 and array.subject.confirmation_index == 7
    assert array.evaluation.reference.candle_index == 7
    assert array.dealing_range is frames[7].dealing_range
    assert array.evaluation.available_at == array.subject.available_at
    assert array.lower_classification == PDClassification.OUTSIDE_RANGE
    assert array.classification == PDClassification.DISCOUNT


def test_new_array_without_range_gets_explicit_insufficient_context_not_fabricated_midpoint():
    frame = run(golden())[2]
    assert frame.arrays
    assert all(
        a.classification == PDClassification.INSUFFICIENT_CONTEXT and a.context is None
        for a in frame.arrays
    )


def test_new_pool_versions_are_annotated_by_evidence_id_not_mutable_pool_id():
    frames = run(golden())
    first = next(a for a in frames[2].arrays if a.kind == PDArrayKind.LIQUIDITY_POOL)
    second = next(a for a in frames[4].arrays if a.subject.pool_id == first.subject.pool_id)
    assert first.source_reference.evidence_id != second.source_reference.evidence_id
    assert first.subject.touch_count == 1 and second.subject.touch_count == 2
    assert first.classification == PDClassification.INSUFFICIENT_CONTEXT
    assert second.classification == PDClassification.PREMIUM
