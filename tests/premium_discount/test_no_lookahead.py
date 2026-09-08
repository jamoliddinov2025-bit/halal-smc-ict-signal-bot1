from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer
from tests.analysis.helpers import bar
from tests.premium_discount.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    golden,
    run,
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
@pytest.mark.parametrize("fraction", ["0", "0.05", "0.5"])
def test_every_prefix_equals_corresponding_full_output_including_ids_and_hashes(seed, fraction):
    candles = seeded(seed)
    full = run(candles, fraction)
    for cut in range(len(candles) + 1):
        prefix = run(candles[:cut], fraction)
        assert prefix == full[:cut]
        assert [f.provenance.evidence_id for f in prefix] == [
            f.provenance.evidence_id for f in full[:cut]
        ]
        assert [f.provenance.input_prefix_hash for f in prefix] == [
            f.provenance.input_prefix_hash for f in full[:cut]
        ]


@pytest.mark.parametrize("seed", [5, 19])
@pytest.mark.parametrize("fraction", ["0", "0.1"])
def test_every_future_price_suffix_can_change_without_reclassifying_history(seed, fraction):
    candles = seeded(seed)
    full = run(candles, fraction)
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert run((*candles[:cut], *future), fraction)[:cut] == full[:cut]


def test_known_all_classification_example_has_no_pending_pivot_leakage():
    full = run(golden())
    assert len({f.classification for f in full}) == 5
    for cut in range(len(golden()) + 1):
        assert run(golden()[:cut]) == full[:cut]
    confirmed = run(golden()[:4])
    invalidated = run((*golden()[:3], bar(3, 8)))
    assert confirmed[:3] == invalidated[:3]
    assert confirmed[3].dealing_range is not None and invalidated[3].dealing_range is None


def test_retained_ranges_equilibria_sidecars_and_source_objects_never_mutate():
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = analyzer()
    retained = tuple(
        pd.update(blocks.update(gaps.update(displacement.update(source.update(c)))))
        for c in golden()
    )
    saved = deepcopy(retained)
    for i in range(7, 22):
        pd.update(
            blocks.update(
                gaps.update(displacement.update(source.update(bar(i, 250 if i % 2 else 8))))
            )
        )
    assert retained == saved == run(golden())


def test_numeric_spelling_does_not_change_any_pd_identity():
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
    assert run(candles, "0.00") == run(golden())


def test_changed_past_volume_changes_lineage_even_with_equal_classifications():
    candles = golden()
    old = run(candles)
    new = run((replace(candles[0], volume=Decimal(9)), *candles[1:]))
    assert [f.classification for f in old] == [f.classification for f in new]
    assert all(
        a.provenance.evidence_id != b.provenance.evidence_id for a, b in zip(old, new, strict=True)
    )
