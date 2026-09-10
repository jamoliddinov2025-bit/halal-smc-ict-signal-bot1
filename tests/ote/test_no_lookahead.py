from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer
from smcsignal.analysis.premium_discount import PDAnalyzer
from tests.analysis.helpers import bar
from tests.ote.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    golden,
    run,
    upstream,
)


def seeded(seed, count=23):
    random = Random(seed)
    last = Decimal(100)
    candles = []
    for i in range(count):
        closing = max(Decimal(20), last + random.randint(-8, 8))
        candles.append(
            bar(
                i,
                closing,
                opening=last,
                high=max(last, closing) + random.randint(0, 3),
                low=min(last, closing) - random.randint(0, 3),
                volume=random.randint(0, 10),
            )
        )
        last = closing
    return tuple(candles)


@pytest.mark.parametrize("seed", [2, 17, 31])
def test_every_prefix_equals_corresponding_full_output_including_ids_and_hashes(seed):
    candles = seeded(seed)
    full = run(candles)
    for cut in range(len(candles) + 1):
        prefix = run(candles[:cut])
        assert prefix == full[:cut]
        assert [f.provenance.evidence_id for f in prefix] == [
            f.provenance.evidence_id for f in full[:cut]
        ]
        assert [f.provenance.input_prefix_hash for f in prefix] == [
            f.provenance.input_prefix_hash for f in full[:cut]
        ]


@pytest.mark.parametrize("seed", [5, 19])
def test_every_future_price_suffix_can_change_without_reclassifying_history(seed):
    candles = seeded(seed)
    full = run(candles)
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert run((*candles[:cut], *future))[:cut] == full[:cut]


def test_documented_example_has_no_pending_pivot_leakage():
    full = run(golden())
    for cut in range(len(golden()) + 1):
        assert run(golden()[:cut]) == full[:cut]
    confirmed = run(golden()[:6])
    invalidated = run((*golden()[:5], bar(5, 8, high=9, low=7)))
    assert confirmed[:5] == invalidated[:5]
    assert confirmed[5].classification != invalidated[5].classification


def test_retained_zones_and_observations_never_mutate():
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    ote = analyzer()
    retained = tuple(
        ote.update(pd.update(blocks.update(gaps.update(displacement.update(source.update(c))))))
        for c in golden()
    )
    saved = deepcopy(retained)
    for i in range(10, 22):
        ote.update(
            pd.update(
                blocks.update(
                    gaps.update(displacement.update(source.update(bar(i, 250 if i % 2 else 8))))
                )
            )
        )
    assert retained == saved == run(golden())


def test_numeric_spelling_does_not_change_any_ote_identity():
    candles = tuple(
        replace(
            c,
            **{
                n: Decimal(str(getattr(c, n)) + ("0" if "." in str(getattr(c, n)) else ".00"))
                for n in ("open", "high", "low", "close", "volume")
            },
        )
        for c in golden()
    )
    assert run(candles) == run(golden())


def test_changed_past_volume_changes_lineage_even_with_equal_classifications():
    candles = golden()
    old, new = run(candles), run((replace(candles[0], volume=Decimal(9)), *candles[1:]))
    assert [f.classification for f in old] == [f.classification for f in new]
    assert all(
        a.provenance.evidence_id != b.provenance.evidence_id for a, b in zip(old, new, strict=True)
    )


def test_upstream_pd_objects_and_ids_are_preserved():
    raw = upstream(golden())
    engine = analyzer()
    frames = tuple(engine.update(frame) for frame in raw)
    for frame, original in zip(frames, raw, strict=True):
        assert frame.upstream is original
        assert frame.upstream.provenance.evidence_id == original.provenance.evidence_id
        if original.dealing_range is not None:
            assert frame.zone.dealing_range is original.dealing_range
            assert frame.zone.range_id == original.dealing_range.range_id
