from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from smcsignal.analysis.displacement import analyze_displacement
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from tests.analysis.helpers import bar
from tests.displacement.helpers import (
    ANALYSIS,
    LIQUIDITY,
    SERIES,
    analyzer,
    bull,
    events,
    golden,
    run,
    settings,
    upstream,
    warmup,
)


def seeded(seed, count=27):
    random = Random(seed)
    previous = Decimal(100)
    result = []
    for index in range(count):
        close = max(Decimal(10), previous + random.randint(-8, 8))
        result.append(
            bar(
                index,
                close,
                opening=previous,
                high=max(previous, close) + random.randint(0, 4),
                low=min(previous, close) - random.randint(0, 4),
                volume=random.randint(0, 9),
            )
        )
        previous = close
    return tuple(result)


@pytest.mark.parametrize("seed", [3, 17, 31])
@pytest.mark.parametrize("period", [1, 3, 14])
def test_every_prefix_including_all_event_ids_and_provenance_equals_full_series(seed, period):
    candles = seeded(seed)
    full = run(candles, atr_period=period)
    for cut in range(len(candles) + 1):
        prefix = run(candles[:cut], atr_period=period)
        assert prefix == full[:cut]
        assert tuple(e.event_id for e in events(prefix)) == tuple(
            e.event_id for e in events(full) if e.detection_index < cut
        )
        assert tuple(f.provenance.input_prefix_hash for f in prefix) == tuple(
            f.provenance.input_prefix_hash for f in full[:cut]
        )


@pytest.mark.parametrize("seed", [2, 29])
@pytest.mark.parametrize("period", [3, 14])
def test_future_candle_price_shocks_cannot_change_any_historical_displacement(seed, period):
    candles = seeded(seed)
    full = run(candles, atr_period=period)
    for cut in range(len(candles) + 1):
        future = tuple(bar(i, 1000 if i % 2 else 10) for i in range(cut, len(candles)))
        assert run((*candles[:cut], *future), atr_period=period)[:cut] == full[:cut]


def test_nonempty_golden_displacements_have_prefix_and_future_invariance():
    candles = golden()
    full = run(candles, atr_period=14)
    assert [e.detection_index for e in events(full)] == [15, 16]
    for cut in range(len(candles) + 1):
        assert run(candles[:cut], atr_period=14) == full[:cut]
        future = tuple(bar(i, 1000 + i) for i in range(cut, len(candles)))
        assert run((*candles[:cut], *future), atr_period=14)[:cut] == full[:cut]


def test_retained_history_and_atr_are_immutable_after_appending_future_candles():
    source = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    detector = analyzer(atr_period=14)
    history = tuple(detector.update(source.update(c)) for c in golden())
    saved = deepcopy(history)
    for i in range(len(golden()), 35):
        detector.update(source.update(bar(i, 200 if i % 2 else 30)))
    assert history == saved == run(golden(), atr_period=14)
    assert events(history) == events(saved)


def test_same_numeric_decimal_spellings_have_identical_ids_and_artifacts():
    candles = (*warmup(), bull())
    rewritten = tuple(
        replace(
            c,
            **{
                name: Decimal(
                    str(getattr(c, name)) + ("0" if "." in str(getattr(c, name)) else ".00")
                )
                for name in ("open", "high", "low", "close", "volume")
            },
        )
        for c in candles
    )
    assert run(rewritten, min_body_atr="1.000", bullish_close_min="0.7000") == run(candles)


def test_changed_past_data_changes_ids_even_when_all_price_detections_are_identical():
    candles = (*warmup(), bull())
    original = run(candles)
    altered = run((replace(candles[0], volume=Decimal(9)), *candles[1:]))
    left, right = events(original)[0], events(altered)[0]
    assert (left.direction, left.body_size, left.range_size) == (
        right.direction,
        right.body_size,
        right.range_size,
    )
    assert left.event_id != right.event_id
    assert left.provenance.input_prefix_hash != right.provenance.input_prefix_hash


def test_configuration_and_source_identity_are_bound_even_if_event_still_qualifies():
    candles = (*warmup(), bull())
    original = run(candles)
    changed_cfg = run(candles, min_body_atr="0.9")
    changed_source = analyze_displacement(
        upstream(candles, series=replace(SERIES, dataset_id="different-origin")),
        settings(),
        price_unit="USDT",
    )
    assert len({events(seq)[0].event_id for seq in (original, changed_cfg, changed_source)}) == 3
    assert original[-1].atr_reference == changed_cfg[-1].atr_reference


def test_future_sweep_cannot_be_attached_to_old_displacement():
    candles = (*warmup(), bull())
    old = run(candles)
    full = run(
        (
            *candles,
            bar(5, 104, opening=102, high=105, low=98),
            bar(6, 99, opening=104, high=106, low=98),
        )
    )
    assert full[: len(candles)] == old
    assert old[-1].events[0].preceding_sweeps == ()
