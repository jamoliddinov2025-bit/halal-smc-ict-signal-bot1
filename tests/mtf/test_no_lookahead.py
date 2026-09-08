from copy import deepcopy
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.mtf import analyze_mtf
from tests.mtf.helpers import (
    EIGHT,
    analyzer,
    bars,
    four_hour_frames,
    hour_frames,
    ote_frames,
    primary_15m,
    run,
)


def seeded(seed, count=17):
    random = Random(seed)
    last = Decimal(100)
    prices = []
    for _ in range(count):
        last = max(Decimal(20), last + random.randint(-8, 8))
        prices.append(last)
    return ote_frames(bars("15m", prices, start=EIGHT), "15m", dataset=f"phase13-seed:{seed}")


@pytest.mark.parametrize("seed", [2, 17, 31])
def test_every_primary_prefix_equals_corresponding_full_output_including_ids(seed):
    candles = seeded(seed)
    higher = {"1h": hour_frames(), "4h": four_hour_frames()}
    full = analyze_mtf(candles, higher)
    for cut in range(len(candles) + 1):
        prefix = analyze_mtf(candles[:cut], higher)
        assert prefix == full[:cut]
        assert [frame.provenance.evidence_id for frame in prefix] == [
            frame.provenance.evidence_id for frame in full[:cut]
        ]


def test_future_htf_candles_cannot_change_already_published_ltf_context():
    full = run()
    truncated = analyze_mtf(primary_15m(), {"1h": hour_frames()[:8], "4h": ()})
    for left, right in zip(full[:4], truncated[:4], strict=True):
        assert left.provenance.evidence_id == right.provenance.evidence_id
        assert left.relations[0].latest is not None
        assert left.relations[1].latest is None


def test_future_primary_prices_cannot_reclassify_historical_mtf_context():
    original = primary_15m()
    full = run(original)
    mixed = ote_frames(
        (
            *[frame.upstream.observation.candle for frame in original[:8]],
            *bars("15m", tuple(range(40, 49)), start=EIGHT.replace(hour=10)),
        ),
        "15m",
    )
    changed = analyze_mtf(mixed, {"1h": hour_frames(), "4h": four_hour_frames()})
    assert [frame.provenance.evidence_id for frame in changed[:8]] == [
        frame.provenance.evidence_id for frame in full[:8]
    ]


def test_retained_snapshots_never_mutate_after_future_updates():
    engine = analyzer()
    retained = tuple(engine.update(frame) for frame in primary_15m())
    saved = deepcopy(retained)
    extra = ote_frames(bars("15m", tuple(range(20, 40)), start=EIGHT), "15m")
    for frame in extra[17:]:
        engine.update(frame)
    assert retained == saved == run()


def test_documented_4h_example_has_no_pending_htf_leakage():
    full = run()
    for cut in range(17):
        prefix = analyze_mtf(primary_15m()[:cut], {"1h": hour_frames(), "4h": four_hour_frames()})
        assert prefix == full[:cut]
    assert full[4].relations[1].latest is None
    assert full[16].relations[1].latest is not None
