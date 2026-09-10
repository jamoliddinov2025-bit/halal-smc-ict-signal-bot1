from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.displacement import DisplacementAnalyzer, DisplacementConfig
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from smcsignal.analysis.order_blocks import CandidateSelection, StructureRequirement
from tests.analysis.helpers import bar
from tests.order_blocks.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    bullish,
    events,
    golden,
    run,
)


def seeded(seed, count=24):
    random = Random(seed)
    previous = Decimal(100)
    result = []
    for i in range(count):
        opening = previous + random.randint(-2, 2)
        close = max(Decimal(20), opening + random.randint(-9, 9))
        result.append(
            bar(
                i,
                close,
                opening=opening,
                high=max(opening, close) + random.randint(0, 3),
                low=min(opening, close) - random.randint(0, 3),
                volume=random.randint(0, 8),
            )
        )
        previous = close
    return tuple(result)


@pytest.mark.parametrize("seed", [2, 17, 31])
@pytest.mark.parametrize(
    "require_fvg,mode",
    [
        (False, StructureRequirement.BOS_OR_CHOCH),
        (False, StructureRequirement.DISPLACEMENT_ONLY),
        (True, StructureRequirement.DISPLACEMENT_ONLY),
    ],
)
def test_every_prefix_preserves_full_events_ids_and_provenance(seed, require_fvg, mode):
    candles = seeded(seed)
    full = run(candles, require_fvg=require_fvg, structure_requirement=mode)
    for cut in range(len(candles) + 1):
        prefix = run(candles[:cut], require_fvg=require_fvg, structure_requirement=mode)
        assert prefix == full[:cut]
        assert [e.event_id for e in events(prefix)] == [
            e.event_id for e in events(full) if e.confirmation_index < cut
        ]
        assert [f.provenance.input_prefix_hash for f in prefix] == [
            f.provenance.input_prefix_hash for f in full[:cut]
        ]


@pytest.mark.parametrize("seed", [5, 19])
@pytest.mark.parametrize("require_fvg", [False, True])
def test_every_future_suffix_can_change_without_rewriting_observable_history(seed, require_fvg):
    candles = seeded(seed)
    full = run(
        candles,
        require_fvg=require_fvg,
        structure_requirement=StructureRequirement.DISPLACEMENT_ONLY,
    )
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert (
            run(
                (*candles[:cut], *future),
                require_fvg=require_fvg,
                structure_requirement=StructureRequirement.DISPLACEMENT_ONLY,
            )[:cut]
            == full[:cut]
        )


@pytest.mark.parametrize("require_fvg,indices", [(False, [20, 22]), (True, [21, 23])])
def test_default_period_nonempty_examples_prove_candidate_vs_publication_causality(
    require_fvg, indices
):
    candles = golden()
    full = run(candles, require_fvg=require_fvg, displacement=DisplacementConfig())
    assert [e.confirmation_index for e in events(full)] == indices
    assert [e.candidate_index for e in events(full)] == [18, 21]
    for cut in range(len(candles) + 1):
        assert (
            run(candles[:cut], require_fvg=require_fvg, displacement=DisplacementConfig())
            == full[:cut]
        )


def test_old_candidate_is_not_an_ob_before_displacement_or_required_fvg():
    full = run(bullish(), require_fvg=True)
    assert not full[4].events and not full[5].events and not full[6].events
    assert full[7].events[0].candidate_index == 4
    assert full[7].events[0].displacement_index == 6
    alternative = run((*bullish()[:7], bar(7, 21, opening=20, high=23, low=16)), require_fvg=True)
    assert alternative[:7] == full[:7]
    assert not alternative[7].events


def test_retained_objects_do_not_change_on_future_touches_or_zone_crosses():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    fvg = FVGAnalyzer()
    detector = analyzer()
    old = tuple(
        detector.update(fvg.update(displacement.update(liquidity.update(c)))) for c in bullish()
    )
    saved = deepcopy(old)
    for i in range(8, 22):
        detector.update(
            fvg.update(displacement.update(liquidity.update(bar(i, 200 if i % 2 else 10))))
        )
    assert old == saved == run(bullish())
    assert not hasattr(events(old)[0], "status")


def test_past_volume_change_changes_identity_not_price_definition():
    candles = bullish()
    first = events(run(candles))[0]
    revised = events(run((replace(candles[0], volume=Decimal(9)), *candles[1:])))[0]
    assert (first.direction, first.zone_lower_boundary, first.zone_upper_boundary) == (
        revised.direction,
        revised.zone_lower_boundary,
        revised.zone_upper_boundary,
    )
    assert first.event_id != revised.event_id


def test_numeric_spelling_does_not_change_identity():
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
    assert run(rewritten) == run(candles)


def test_configuration_changes_identity_even_if_same_candidate_is_selected():
    first = events(run(bullish()))[0]
    other = events(run(bullish(), candidate_selection=CandidateSelection.EARLIEST))[0]
    assert first.candidate_index == other.candidate_index
    assert first.event_id != other.event_id
