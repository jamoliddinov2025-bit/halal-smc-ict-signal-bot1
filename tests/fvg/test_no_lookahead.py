from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.fvg import analyze_fvg
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from tests.analysis.helpers import bar
from tests.fvg.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    associated,
    bullish,
    events,
    golden,
    run,
    settings,
    upstream,
)


def seeded(seed, count=24):
    random = Random(seed)
    previous = Decimal(100)
    output = []
    for index in range(count):
        opening = previous + random.randint(-3, 3)
        close = max(Decimal(20), opening + random.randint(-8, 8))
        output.append(
            bar(
                index,
                close,
                opening=opening,
                high=max(opening, close) + random.randint(0, 3),
                low=min(opening, close) - random.randint(0, 3),
                volume=random.randint(0, 10),
            )
        )
        previous = close
    return tuple(output)


@pytest.mark.parametrize("seed", [2, 17, 31])
@pytest.mark.parametrize("minimum,require", [("0", False), ("1", False), ("0", True)])
def test_every_prefix_preserves_full_evidence_ids_and_hashes(seed, minimum, require):
    candles = seeded(seed)
    full = run(candles, minimum=minimum, require=require)
    for cut in range(len(candles) + 1):
        prefix = run(candles[:cut], minimum=minimum, require=require)
        assert prefix == full[:cut]
        assert [e.event_id for e in events(prefix)] == [
            e.event_id for e in events(full) if e.detection_index < cut
        ]
        assert [f.provenance.input_prefix_hash for f in prefix] == [
            f.provenance.input_prefix_hash for f in full[:cut]
        ]


@pytest.mark.parametrize("seed", [5, 19])
@pytest.mark.parametrize("require", [False, True])
def test_every_future_suffix_can_change_without_repainting_any_old_record(seed, require):
    candles = seeded(seed)
    full = run(candles, require=require)
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert run((*candles[:cut], *future), require=require)[:cut] == full[:cut]


def test_hand_computed_nonempty_default_events_have_prefix_future_invariance():
    candles = golden()
    full = run(candles, displacement=DisplacementConfig())
    assert [e.detection_index for e in events(full)] == [16, 19]
    for cut in range(len(candles) + 1):
        assert run(candles[:cut], displacement=DisplacementConfig()) == full[:cut]
        changed = (*candles[:cut], *(bar(i, 200 + i) for i in range(cut, len(candles))))
        assert run(changed, displacement=DisplacementConfig())[:cut] == full[:cut]


def test_same_c1_c2_can_lead_to_gap_or_no_gap_but_no_provisional_record():
    shared = bullish()[:2]
    yes = run((*shared, bullish()[2]))
    no = run((*shared, bar(2, 101, opening=101, high=102, low=100)))
    assert yes[:2] == no[:2] == run(shared)
    assert not events(yes[:2]) and yes[2].events and not no[2].events


def test_future_fill_touch_or_gap_through_does_not_mutate_creation_evidence():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    engine = analyzer()
    retained = tuple(engine.update(displacement.update(liquidity.update(c))) for c in bullish())
    copy = deepcopy(retained)
    for c in (
        bar(3, 102, opening=104, high=105, low=100),
        bar(4, 100, opening=99, high=101, low=90),
        bar(5, 103, opening=100, high=110, low=100),
    ):
        engine.update(displacement.update(liquidity.update(c)))
    assert retained == copy == run(bullish())
    assert not hasattr(retained[2].events[0], "status")  # no misleading mutable OPEN label


def test_numeric_spelling_and_ambient_representation_do_not_change_ids():
    candles = bullish()
    rewritten = tuple(
        replace(
            c,
            **{
                name: Decimal(str(getattr(c, name)) + ".00")
                for name in ("open", "high", "low", "close", "volume")
            },
        )
        for c in candles
    )
    assert run(rewritten, minimum="0.000") == run(candles)


def test_changed_past_raw_volume_changes_provenance_even_when_geometry_is_equal():
    candles = bullish()
    before = run(candles)[2].events[0]
    after = run((replace(candles[0], volume=Decimal(9)), *candles[1:]))[2].events[0]
    assert (before.direction, before.gap_size) == (after.direction, after.gap_size)
    assert (
        before.event_id != after.event_id
        and before.provenance.input_prefix_hash != after.provenance.input_prefix_hash
    )


def test_configuration_and_source_namespace_are_in_identity_without_modifying_upstream():
    source = upstream(bullish())
    base = analyze_fvg(source)[2].events[0]
    different = analyze_fvg(source, settings("0.1"))[2].events[0]
    other_source = analyze_fvg(
        upstream(bullish(), series=replace(SERIES, dataset_id="different-origin"))
    )[2].events[0]
    assert len({base.event_id, different.event_id, other_source.event_id}) == 3
    assert base.window[1] is different.window[1] is source[1]


def test_future_displacement_cannot_be_substituted_for_middle_event():
    candles = associated()
    full = run(
        (*candles, bar(4, 90, opening=107, high=108, low=89)),
        displacement=DisplacementConfig(atr_period=1),
    )
    prefix = run(candles, displacement=DisplacementConfig(atr_period=1))
    assert full[:4] == prefix
    assert prefix[3].events[0].associated_displacement.detection_index == 2
