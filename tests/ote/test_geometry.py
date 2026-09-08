from decimal import ROUND_UP, Decimal, Inexact, localcontext

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.ote import OTEConfig, OTEDirection
from smcsignal.analysis.ote.calculation import ordered_bounds, retracement_price, zone_geometry
from tests.ote.helpers import (
    BULLISH_HIGH,
    BULLISH_LOW,
    BULLISH_OTE_LOWER,
    BULLISH_OTE_UPPER,
    bearish_stable,
    golden,
    run,
)


def test_bullish_formula_is_high_minus_ratio_times_span():
    span = BULLISH_HIGH - BULLISH_LOW
    assert retracement_price(BULLISH_LOW, BULLISH_HIGH, Decimal("0.62"), OTEDirection.BULLISH) == (
        BULLISH_HIGH - Decimal("0.62") * span
    )
    assert retracement_price(BULLISH_LOW, BULLISH_HIGH, Decimal("0.79"), OTEDirection.BULLISH) == (
        BULLISH_HIGH - Decimal("0.79") * span
    )


def test_bearish_formula_is_low_plus_ratio_times_span():
    low, high, span = Decimal(11), Decimal(16), Decimal(5)
    assert retracement_price(low, high, Decimal("0.62"), OTEDirection.BEARISH) == (
        low + Decimal("0.62") * span
    )
    assert retracement_price(low, high, Decimal("0.79"), OTEDirection.BEARISH) == (
        low + Decimal("0.79") * span
    )


def test_bullish_62_is_above_79_and_the_stored_interval_is_ordered():
    lower_r, upper_r, lower, upper = zone_geometry(
        BULLISH_LOW, BULLISH_HIGH, Decimal("0.62"), Decimal("0.79"), OTEDirection.BULLISH
    )
    assert lower_r == BULLISH_OTE_UPPER
    assert upper_r == BULLISH_OTE_LOWER
    assert (lower, upper) == (BULLISH_OTE_LOWER, BULLISH_OTE_UPPER)
    assert ordered_bounds(lower_r, upper_r) == (lower, upper)


def test_bearish_62_is_below_79_and_needs_no_swap():
    lower_r, upper_r, lower, upper = zone_geometry(
        Decimal(11), Decimal(16), Decimal("0.62"), Decimal("0.79"), OTEDirection.BEARISH
    )
    assert (lower_r, upper_r) == (Decimal("14.1"), Decimal("14.95"))
    assert (lower, upper) == (Decimal("14.1"), Decimal("14.95"))


def test_pipeline_zone_matches_hand_computed_default_geometry():
    bullish = run(golden())[4].zone
    assert bullish.direction == OTEDirection.BULLISH
    assert (bullish.range_low, bullish.range_high, bullish.range_size) == (
        BULLISH_LOW,
        BULLISH_HIGH,
        Decimal(32),
    )
    assert bullish.lower_retracement_price == BULLISH_OTE_UPPER
    assert bullish.upper_retracement_price == BULLISH_OTE_LOWER
    assert (bullish.lower_boundary, bullish.upper_boundary) == (
        BULLISH_OTE_LOWER,
        BULLISH_OTE_UPPER,
    )
    assert bullish.zone_size == Decimal("5.44")
    bearish = run(bearish_stable())[3].zone
    assert bearish.direction == OTEDirection.BEARISH
    assert (bearish.lower_boundary, bearish.upper_boundary) == (
        Decimal("14.1"),
        Decimal("14.95"),
    )


def test_configured_0_62_is_not_silently_replaced_with_0_618():
    exact = retracement_price(BULLISH_LOW, BULLISH_HIGH, Decimal("0.62"), OTEDirection.BULLISH)
    classic = retracement_price(BULLISH_LOW, BULLISH_HIGH, Decimal("0.618"), OTEDirection.BULLISH)
    assert exact != classic
    assert exact == BULLISH_OTE_UPPER
    default, classic_cfg = (
        run(golden()),
        run(golden(), OTEConfig(Decimal("0.618"), Decimal("0.786"))),
    )
    assert default[5].zone.lower_boundary != classic_cfg[5].zone.lower_boundary
    assert default[5].upstream == classic_cfg[5].upstream


def test_zone_arithmetic_does_not_inherit_ambient_precision():
    expected = retracement_price(BULLISH_LOW, BULLISH_HIGH, Decimal("0.62"), OTEDirection.BULLISH)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert (
            retracement_price(BULLISH_LOW, BULLISH_HIGH, Decimal("0.62"), OTEDirection.BULLISH)
            == expected
            == BULLISH_OTE_UPPER
        )
        assert run(golden())[5].zone.lower_boundary == BULLISH_OTE_LOWER


def test_near_zero_prices_keep_exact_scaled_geometry():
    from dataclasses import replace

    candles = tuple(
        replace(c, **{n: getattr(c, n).scaleb(-40) for n in ("open", "high", "low", "close")})
        for c in golden()
    )
    frame = run(candles)[5]
    assert frame.zone.lower_boundary == Decimal("15.72e-40")
    assert frame.zone.upper_boundary == Decimal("21.16e-40")
    assert frame.classification.value == "INSIDE_OTE"


def test_unsupported_direction_and_non_decimal_ratio_fail_explicitly():
    with pytest.raises(AnalysisInputError):
        retracement_price(BULLISH_LOW, BULLISH_HIGH, Decimal("0.62"), "ranging")
    with pytest.raises(AnalysisInputError):
        retracement_price(BULLISH_LOW, BULLISH_HIGH, 0.62, OTEDirection.BULLISH)
