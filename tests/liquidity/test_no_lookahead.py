from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from tests.analysis.helpers import bar
from tests.liquidity.helpers import analyzer, golden, run, sweeps


def seeded(seed, count=26):
    random = Random(seed)
    previous = Decimal(100)
    result = []
    for i in range(count):
        close = previous + random.randint(-4, 4)
        result.append(
            bar(
                i,
                close,
                opening=previous,
                high=max(close, previous) + random.randint(1, 4),
                low=min(close, previous) - random.randint(1, 4),
                volume=random.randint(0, 10),
            )
        )
        previous = close
    return tuple(result)


@pytest.mark.parametrize("seed", [2, 7, 31])
@pytest.mark.parametrize("length,bps", [(3, "0"), (5, "10"), (7, "100")])
def test_every_prefix_matches_full_series_including_all_provenance(seed, length, bps):
    candles = seeded(seed)
    full = run(candles, length=length, bps=bps)
    for cut in range(len(candles) + 1):
        assert run(candles[:cut], length=length, bps=bps) == full[:cut]


@pytest.mark.parametrize("seed", [5, 19])
@pytest.mark.parametrize("length,bps", [(3, "0"), (5, "100")])
def test_replacing_every_future_price_cannot_change_the_past(seed, length, bps):
    candles = seeded(seed)
    full = run(candles, length=length, bps=bps)
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert run((*candles[:cut], *future), length=length, bps=bps)[:cut] == full[:cut]


def test_golden_nonempty_sweeps_have_prefix_stable_ids_and_hashes():
    candles = golden()
    full = run(candles)
    assert len(sweeps(full)) == 2
    for cut in range(len(candles) + 1):
        prefix = run(candles[:cut])
        assert prefix == full[:cut]
        assert sweeps(prefix) == tuple(
            e for e in sweeps(full) if e.breach.reference.candle_index < cut
        )
        assert (
            run((*candles[:cut], *(bar(i, 500 + i) for i in range(cut, len(candles)))))[:cut]
            == full[:cut]
        )


def test_appending_future_changes_neither_pools_events_nor_retained_objects():
    engine = analyzer()
    retained = tuple(engine.update(c) for c in golden())
    saved = deepcopy(retained)
    active = engine.active_pools
    for i in range(len(golden()), 35):
        engine.update(bar(i, 200 + i if i % 2 else 5))
    assert retained == saved == run(golden())
    assert all(p.status.value == "active" for p in active)


def test_pending_pivot_can_be_invalidated_without_historical_publication():
    prefix = (bar(0, 10), bar(1, 15))
    confirmed = run((*prefix, bar(2, 12)))
    disqualified = run((*prefix, bar(2, 20)))
    assert confirmed[:2] == disqualified[:2] == run(prefix)
    assert confirmed[2].confirmed_swings
    assert not disqualified[2].confirmed_swings


def test_snapshot_identity_does_not_depend_on_decimal_spelling():
    candles = golden()
    alternate = tuple(
        replace(
            c,
            **{
                field: Decimal(str(getattr(c, field)) + ".00")
                for field in ("open", "high", "low", "close", "volume")
            },
        )
        for c in candles
    )
    assert run(alternate, bps="0.00") == run(candles)
