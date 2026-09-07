"""Metadata-only contracts: traceability, immutable snapshots, and availability."""

from dataclasses import FrozenInstanceError, dataclass, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256

import pytest

from smcsignal.analysis import (
    AnalysisInputError,
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    ProvenancedEvidence,
    SeriesProvenance,
)

BASE = datetime(2024, 1, 1, tzinfo=UTC)
STEP = timedelta(minutes=15)
SERIES = SeriesProvenance("BTCUSDT", "15m", "spot", "csv", "synthetic-fixture")
CONFIG_DIGEST = sha256(b'{"fractal_length": 5}').hexdigest()
PREFIX_DIGEST = sha256(b"synthetic closed-candle prefix only").hexdigest()


def candle(index=0):
    return CandleReference(SERIES, index, BASE + index * STEP, BASE + (index + 1) * STEP)


def provenance(**overrides):
    return EvidenceProvenance(
        **{
            "evidence_id": "fixture:evidence:at-first-close",
            "series": SERIES,
            "producer": "fixture-producer",
            "producer_version": "fixture-version",
            "configuration_hash": CONFIG_DIGEST,
            "input_prefix_hash": PREFIX_DIGEST,
            "available_at": BASE + STEP,
            "source_candles": (candle(),),
            **overrides,
        }
    )


def test_exact_source_and_producer_context_is_retained():
    record = provenance()
    assert record.series is SERIES
    assert record.configuration_hash == CONFIG_DIGEST
    assert record.input_prefix_hash == PREFIX_DIGEST
    assert record.producer_version == "fixture-version"
    assert record.source_candles == (candle(),)


def test_reference_pins_snapshot_not_mutable_latest_entity():
    original = provenance()
    reference = original.as_reference()
    later = replace(original, evidence_id="fixture:evidence:later", available_at=BASE + 2 * STEP)
    assert reference == EvidenceReference(original.evidence_id, SERIES, BASE + STEP)
    assert reference.evidence_id != later.evidence_id
    assert reference.available_at == original.available_at


def test_contract_is_composable_without_changing_existing_feature_classes():
    @dataclass(frozen=True)
    class RawFacts:
        provenance: EvidenceProvenance
        source_label: str

    def reference_of(record: ProvenancedEvidence) -> EvidenceReference:
        return record.provenance.as_reference()

    facts = RawFacts(provenance(), "synthetic raw facts")
    assert reference_of(facts) == provenance().as_reference()


def test_all_provenance_layers_are_immutable():
    record = provenance()
    for instance, name, value in [
        (record, "producer", "other"),
        (SERIES, "symbol", "OTHER"),
        (record.source_candles[0], "candle_index", 5),
        (record.as_reference(), "evidence_id", "other"),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(instance, name, value)


@pytest.mark.parametrize("name", ["symbol", "timeframe", "venue", "provider", "dataset_id"])
@pytest.mark.parametrize("value", ["", " ", " untrimmed", None])
def test_invalid_series_identity(name, value):
    with pytest.raises(AnalysisInputError):
        replace(SERIES, **{name: value})


@pytest.mark.parametrize("name", ["evidence_id", "producer", "producer_version"])
def test_empty_record_identity_fields_fail(name):
    with pytest.raises(AnalysisInputError):
        provenance(**{name: ""})


@pytest.mark.parametrize("name", ["configuration_hash", "input_prefix_hash"])
@pytest.mark.parametrize("value", ["", "sha256:missing", "g" * 64, "a" * 63, None])
def test_invalid_fingerprints_are_rejected(name, value):
    with pytest.raises(AnalysisInputError, match="SHA-256"):
        provenance(**{name: value})


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_unsupported_schema_versions(version):
    with pytest.raises(AnalysisInputError, match="schema_version"):
        provenance(schema_version=version)


@pytest.mark.parametrize("index", [-1, True, 1.5])
def test_invalid_candle_indices(index):
    with pytest.raises(AnalysisInputError, match="candle_index"):
        replace(candle(), candle_index=index)


def test_candle_requires_explicit_positive_closure_interval():
    with pytest.raises(AnalysisInputError, match="closed_at"):
        replace(candle(), closed_at=BASE)


def test_open_timestamp_cannot_be_used_as_knowable_time():
    with pytest.raises(AnalysisInputError, match="not closed"):
        provenance(available_at=BASE)


def test_future_source_candle_is_rejected():
    with pytest.raises(AnalysisInputError, match="not closed"):
        provenance(source_candles=(candle(), candle(1)))


def test_candle_at_its_exclusive_close_boundary_is_available():
    assert provenance().available_at == candle().closed_at


def test_source_indices_are_local_to_the_declared_series():
    wrong_series = replace(SERIES, timeframe="1h")
    with pytest.raises(AnalysisInputError, match="declared series"):
        provenance(source_candles=(replace(candle(), series=wrong_series),))


@pytest.mark.parametrize("refs", [(candle(), candle()), (candle(1), candle())])
def test_duplicate_or_reversed_source_references_are_rejected(refs):
    with pytest.raises(AnalysisInputError, match="chronological"):
        provenance(available_at=BASE + 2 * STEP, source_candles=refs)


def test_sparse_ordered_references_are_allowed_without_filling_gaps():
    record = provenance(available_at=BASE + 4 * STEP, source_candles=(candle(), candle(3)))
    assert [ref.candle_index for ref in record.source_candles] == [0, 3]


def test_cross_timeframe_dependencies_use_availability_not_local_index():
    htf = replace(SERIES, timeframe="1h")
    known = EvidenceReference("htf:closed-snapshot", htf, BASE + STEP)
    record = provenance(dependencies=(known,))
    assert record.dependencies == (known,)
    future = replace(known, available_at=BASE + timedelta(hours=1))
    with pytest.raises(AnalysisInputError, match="not available"):
        provenance(dependencies=(future,))


def test_timezone_offsets_normalize_to_same_instant():
    offset_time = datetime(2024, 1, 1, 5, 15, tzinfo=timezone(timedelta(hours=5)))
    assert provenance(available_at=offset_time).available_at == BASE + STEP


def test_submillisecond_arrival_times_are_not_rounded_into_the_past():
    instant = BASE + STEP + timedelta(microseconds=1)
    dependency = EvidenceReference("slightly-later", SERIES, instant)
    assert dependency.available_at == instant
    with pytest.raises(AnalysisInputError, match="not available"):
        provenance(dependencies=(dependency,))
    assert provenance(available_at=instant, dependencies=(dependency,)).available_at == instant


@pytest.mark.parametrize("instant", [datetime(2024, 1, 1), "2024-01-01T00:15:00Z", None])
def test_naive_or_invalid_availability_is_rejected(instant):
    with pytest.raises(AnalysisInputError, match="timezone-aware"):
        provenance(available_at=instant)


def test_self_reference_and_duplicate_dependencies_are_rejected():
    record = provenance()
    with pytest.raises(AnalysisInputError, match="self-references"):
        provenance(dependencies=(record.as_reference(),))
    dependency = EvidenceReference("other-snapshot", SERIES, BASE + STEP)
    with pytest.raises(AnalysisInputError, match="duplicate"):
        provenance(dependencies=(dependency, dependency))


@pytest.mark.parametrize("updates", [{"source_candles": []}, {"dependencies": []}])
def test_mutable_collections_are_rejected(updates):
    with pytest.raises(AnalysisInputError, match="immutable tuples"):
        provenance(**updates)


def test_non_candle_sources_can_supply_metadata_without_fake_candles():
    record = provenance(source_candles=())
    assert record.source_candles == ()
    assert record.dependencies == ()


def test_contract_contains_no_evaluation_or_signal_policy_fields():
    forbidden = {
        "score",
        "quality_score",
        "weight",
        "confidence",
        "rank",
        "threshold",
        "signal_count",
    }
    for model in (SeriesProvenance, CandleReference, EvidenceReference, EvidenceProvenance):
        assert not forbidden.intersection(field.name for field in fields(model))


def test_old_provenance_does_not_change_when_a_later_snapshot_is_created():
    first = provenance()
    before = first.as_reference()
    later = provenance(
        evidence_id="fixture:evidence:next-close",
        input_prefix_hash=sha256(b"synthetic prefix extended by one closed candle").hexdigest(),
        available_at=BASE + 2 * STEP,
        source_candles=(candle(), candle(1)),
        dependencies=(before,),
    )
    assert first.as_reference() == before
    assert first.input_prefix_hash == PREFIX_DIGEST
    assert later.dependencies == (before,)
