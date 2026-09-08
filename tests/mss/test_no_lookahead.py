from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.displacement import DisplacementConfig
from smcsignal.analysis.mss import analyze_mss
from tests.analysis.helpers import bar
from tests.mss.helpers import SERIES, analyzer, base, consecutive, events, golden, run, upstream


def seeded(seed, count=23):
    random = Random(seed)
    previous = Decimal(100)
    result = []
    for i in range(count):
        close = max(Decimal(20), previous + random.randint(-10, 10))
        result.append(
            bar(
                i,
                close,
                opening=previous,
                high=max(close, previous) + random.randint(0, 3),
                low=min(close, previous) - random.randint(0, 3),
                volume=random.randint(0, 10),
            )
        )
        previous = close
    return tuple(result)


@pytest.mark.parametrize("seed", [2, 17, 31])
@pytest.mark.parametrize("period", [1, 3, 14])
def test_every_prefix_preserves_all_historical_events_ids_and_provenance(seed, period):
    candles = seeded(seed)
    full = run(candles, displacement=DisplacementConfig(atr_period=period))
    for cut in range(len(candles) + 1):
        prefix = run(candles[:cut], displacement=DisplacementConfig(atr_period=period))
        assert prefix == full[:cut]
        assert [e.event_id for e in events(prefix)] == [
            e.event_id for e in events(full) if e.detection_index < cut
        ]
        assert [f.provenance.input_prefix_hash for f in prefix] == [
            f.provenance.input_prefix_hash for f in full[:cut]
        ]


@pytest.mark.parametrize("seed", [5, 19])
@pytest.mark.parametrize("period", [3, 14])
def test_arbitrary_future_prices_cannot_alter_any_published_output(seed, period):
    candles = seeded(seed)
    full = run(candles, displacement=DisplacementConfig(atr_period=period))
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert (
            run((*candles[:cut], *future), displacement=DisplacementConfig(atr_period=period))[:cut]
            == full[:cut]
        )


@pytest.mark.parametrize("fixture,indices", [(golden, [22, 27]), (consecutive, [21, 22])])
def test_nonempty_hand_computed_mss_and_consecutive_cases_have_prefix_identity(fixture, indices):
    candles = fixture()
    full = run(candles, displacement=DisplacementConfig())
    assert [e.detection_index for e in events(full)] == indices
    for cut in range(len(candles) + 1):
        assert run(candles[:cut], displacement=DisplacementConfig()) == full[:cut]


def test_retained_models_and_original_evidence_do_not_mutate_on_future_updates():
    raw = upstream((*base(), *(bar(i, 400 if i % 2 else 5) for i in range(14, 22))))
    engine = analyzer()
    before = tuple(engine.update(f) for f in raw[:14])
    frozen = deepcopy(before)
    for frame in raw[14:]:
        engine.update(frame)
    assert before == frozen == run(base())
    assert [e.event_id for e in events(before)] == [e.event_id for e in events(frozen)]


def test_extending_future_context_never_adds_old_fvg_ob_or_sweep_relationships():
    candles = base()[:9]
    before = run(candles)
    after = run(
        (*candles, bar(9, 10, opening=11, high=11, low=8), bar(10, 40, opening=10, high=41, low=9))
    )
    assert after[:9] == before
    assert (
        before[8].events[0].evidence.concurrent_fvgs == after[8].events[0].evidence.concurrent_fvgs
    )
    assert (
        before[8].events[0].evidence.displacement_order_blocks
        == after[8].events[0].evidence.displacement_order_blocks
    )


def test_numeric_spelling_does_not_change_identity():
    rewritten = tuple(
        replace(
            c,
            **{
                name: Decimal(str(getattr(c, name)) + ".00")
                for name in ("open", "high", "low", "close", "volume")
            },
        )
        for c in base()
    )
    assert run(rewritten) == run(base())


def test_changed_past_volume_binds_new_identity_even_if_price_shift_is_same():
    candles = base()
    old = run(candles)
    changed = run((replace(candles[0], volume=Decimal(9)), *candles[1:]))
    assert [(e.detection_index, e.direction) for e in events(old)] == [
        (e.detection_index, e.direction) for e in events(changed)
    ]
    assert {e.event_id for e in events(old)}.isdisjoint(e.event_id for e in events(changed))


def test_source_namespace_is_in_identity_and_no_upstream_id_is_rewritten():
    raw = upstream(base())
    before = [f.provenance.evidence_id for f in raw]
    first = analyze_mss(raw)
    second = analyze_mss(upstream(base(), series=replace(SERIES, dataset_id="different-origin")))
    assert [f.provenance.evidence_id for f in raw] == before
    assert all(f.upstream is original for f, original in zip(first, raw, strict=True))
    assert {e.event_id for e in events(first)}.isdisjoint(e.event_id for e in events(second))
