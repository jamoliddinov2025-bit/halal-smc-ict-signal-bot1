from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.breaker_blocks import analyze_breaker_blocks
from smcsignal.analysis.displacement import DisplacementConfig
from tests.analysis.helpers import bar
from tests.breaker_blocks.helpers import (
    DISPLACEMENT_ONLY,
    SERIES,
    analyzer,
    base,
    events,
    golden,
    multiple,
    run,
    upstream,
)


def seeded(seed, count=23):
    random = Random(seed)
    last = Decimal(100)
    result = []
    for index in range(count):
        close = max(Decimal(20), last + random.randint(-10, 10))
        result.append(
            bar(
                index,
                close,
                opening=last,
                high=max(last, close) + random.randint(0, 3),
                low=min(last, close) - random.randint(0, 3),
                volume=random.randint(0, 10),
            )
        )
        last = close
    return tuple(result)


@pytest.mark.parametrize("seed", [2, 17, 31])
@pytest.mark.parametrize("period", [1, 3, 14])
def test_every_prefix_equals_full_history_including_rejections_ids_and_hashes(seed, period):
    candles = seeded(seed)
    full = run(
        candles, displacement=DisplacementConfig(atr_period=period), order_blocks=DISPLACEMENT_ONLY
    )
    for cut in range(len(candles) + 1):
        prefix = run(
            candles[:cut],
            displacement=DisplacementConfig(atr_period=period),
            order_blocks=DISPLACEMENT_ONLY,
        )
        assert prefix == full[:cut]
        assert [e.event_id for e in events(prefix)] == [
            e.event_id for e in events(full) if e.confirmation_index < cut
        ]
        assert [f.provenance.input_prefix_hash for f in prefix] == [
            f.provenance.input_prefix_hash for f in full[:cut]
        ]


@pytest.mark.parametrize("seed", [5, 19])
@pytest.mark.parametrize("period", [3, 14])
def test_every_future_suffix_can_change_without_repainting_breakers(seed, period):
    candles = seeded(seed)
    full = run(
        candles, displacement=DisplacementConfig(atr_period=period), order_blocks=DISPLACEMENT_ONLY
    )
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert (
            run(
                (*candles[:cut], *future),
                displacement=DisplacementConfig(atr_period=period),
                order_blocks=DISPLACEMENT_ONLY,
            )[:cut]
            == full[:cut]
        )


def test_nonempty_default_both_direction_example_proves_prefix_stability():
    candles = golden()
    full = run(candles, displacement=DisplacementConfig())
    assert [(e.original_ob_confirmation_index, e.confirmation_index) for e in events(full)] == [
        (20, 22),
        (22, 27),
    ]
    for cut in range(len(candles) + 1):
        assert run(candles[:cut], displacement=DisplacementConfig()) == full[:cut]


def test_multiple_consecutive_confirmations_keep_original_ids_across_prefixes():
    candles = multiple()
    full = run(candles, displacement=DisplacementConfig(), order_blocks=DISPLACEMENT_ONLY)
    assert len(events(full)) == 4
    for cut in range(len(candles) + 1):
        assert (
            run(candles[:cut], displacement=DisplacementConfig(), order_blocks=DISPLACEMENT_ONLY)
            == full[:cut]
        )


def test_origins_are_not_breakers_at_candidate_or_ob_publication_time():
    frames = run(base())
    assert not frames[4].events and not frames[6].events
    event = frames[8].events[0]
    assert event.original_candidate_index == 4 and event.original_ob_confirmation_index == 6
    assert event.confirmation_index == 8
    assert event.original_ob_available_at < event.available_at


def test_retained_breaker_and_original_ob_never_mutate_after_future_interactions():
    candles = (*base(), *(bar(i, 300 if i % 2 else 5) for i in range(14, 23)))
    source = upstream(candles)
    engine = analyzer()
    original = tuple(engine.update(frame) for frame in source[:14])
    frozen = deepcopy(original)
    for frame in source[14:]:
        engine.update(frame)
    assert original == frozen == run(base())


def test_future_evidence_does_not_change_first_rejection_or_later_recover_it():
    candles = multiple(first_failure=True)
    full = run(candles, displacement=DisplacementConfig(), order_blocks=DISPLACEMENT_ONLY)
    prefix = run(candles[:19], displacement=DisplacementConfig(), order_blocks=DISPLACEMENT_ONLY)
    assert prefix == full[:19]
    ids = {e.original_ob_id for e in prefix[18].evidence}
    assert ids and all(e.original_ob_id not in ids for e in events(full))


def test_numerically_equal_decimal_spelling_preserves_all_ids():
    rewritten = tuple(
        replace(
            c,
            **{
                field: Decimal(str(getattr(c, field)) + ".00")
                for field in ("open", "high", "low", "close", "volume")
            },
        )
        for c in base()
    )
    assert run(rewritten) == run(base())


def test_changed_past_source_volume_changes_provenance_not_rule_definition():
    candles = base()
    before = run(candles)
    after = run((replace(candles[0], volume=Decimal(9)), *candles[1:]))
    assert [
        (e.confirmation_index, e.direction, e.lower_boundary, e.upper_boundary)
        for e in events(before)
    ] == [
        (e.confirmation_index, e.direction, e.lower_boundary, e.upper_boundary)
        for e in events(after)
    ]
    assert {e.event_id for e in events(before)}.isdisjoint(e.event_id for e in events(after))


def test_source_namespace_binds_identity_without_rewriting_source_objects():
    source = upstream(base())
    initial = [f.provenance.evidence_id for f in source]
    first = analyze_breaker_blocks(source)
    other = analyze_breaker_blocks(
        upstream(base(), series=replace(SERIES, dataset_id="different-origin"))
    )
    assert initial == [f.provenance.evidence_id for f in source]
    assert {e.event_id for e in events(first)}.isdisjoint(e.event_id for e in events(other))
