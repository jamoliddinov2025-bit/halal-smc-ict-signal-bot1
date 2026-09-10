from dataclasses import replace
from decimal import ROUND_UP, Decimal, Inexact, localcontext

import pytest

from smcsignal.analysis.premium_discount import PDClassification
from smcsignal.analysis.premium_discount.calculation import midpoint
from tests.analysis.helpers import bar
from tests.premium_discount.helpers import golden, run


def test_documented_sequence_covers_all_five_classifications():
    frames = run(golden())
    assert [f.classification for f in frames] == [
        PDClassification.INSUFFICIENT_CONTEXT,
        PDClassification.INSUFFICIENT_CONTEXT,
        PDClassification.INSUFFICIENT_CONTEXT,
        PDClassification.PREMIUM,
        PDClassification.DISCOUNT,
        PDClassification.EQUILIBRIUM,
        PDClassification.OUTSIDE_RANGE,
    ]
    assert all(f.evaluated_price == f.observation.candle.close for f in frames)


@pytest.mark.parametrize(
    "close,expected",
    [
        ("10.999999", PDClassification.OUTSIDE_RANGE),
        ("11", PDClassification.DISCOUNT),
        ("13.499999999999", PDClassification.DISCOUNT),
        ("13.5", PDClassification.EQUILIBRIUM),
        ("13.500000000001", PDClassification.PREMIUM),
        ("16", PDClassification.PREMIUM),
        ("16.00000001", PDClassification.OUTSIDE_RANGE),
    ],
)
def test_exact_range_and_equilibrium_boundaries(close, expected):
    frame = run((*golden()[:5], bar(5, close)))[5]
    assert (frame.dealing_range.lower_boundary, frame.dealing_range.upper_boundary) == (11, 16)
    assert frame.classification == expected


@pytest.mark.parametrize(
    "close,expected",
    [
        ("12.99999999", PDClassification.DISCOUNT),
        ("13", PDClassification.EQUILIBRIUM),
        ("13.5", PDClassification.EQUILIBRIUM),
        ("14", PDClassification.EQUILIBRIUM),
        ("14.00000001", PDClassification.PREMIUM),
        ("17", PDClassification.OUTSIDE_RANGE),
    ],
)
def test_configured_equilibrium_half_width_is_fraction_of_span_inclusive(close, expected):
    frame = run((*golden()[:5], bar(5, close)), fraction="0.1")[5]
    assert frame.equilibrium.midpoint == Decimal("13.5")
    assert frame.equilibrium.half_width == Decimal("0.5")
    assert (frame.equilibrium.lower_boundary, frame.equilibrium.upper_boundary) == (13, 14)
    assert frame.classification == expected


def test_maximum_half_width_covers_whole_range_but_not_outside():
    for close, expected in [
        (11, PDClassification.EQUILIBRIUM),
        (16, PDClassification.EQUILIBRIUM),
        (17, PDClassification.OUTSIDE_RANGE),
    ]:
        frame = run((*golden()[:5], bar(5, close)), fraction="0.5")[5]
        assert frame.classification == expected


def test_midpoint_and_zone_arithmetic_do_not_inherit_ambient_precision():
    expected = run(golden(), fraction="0.01234567890123456789")
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert run(golden(), fraction="0.01234567890123456789") == expected
        assert midpoint(
            Decimal("1000000000000000000000000.01"), Decimal("1000000000000000000000000.02")
        ) == Decimal("1000000000000000000000000.015")


def test_positive_near_zero_prices_keep_exact_midpoint_and_classification():
    candles = tuple(
        replace(c, **{n: getattr(c, n).scaleb(-40) for n in ("open", "high", "low", "close")})
        for c in golden()
    )
    a, b = run(golden()), run(candles)
    assert [f.classification for f in a] == [f.classification for f in b]
    assert b[3].equilibrium.midpoint == Decimal("13.5e-40")


def test_equilibrium_configuration_changes_no_upstream_or_range_identity():
    narrow, wider = run(golden()), run(golden(), fraction="0.1")
    for a, b in zip(narrow, wider, strict=True):
        assert a.upstream == b.upstream
        assert a.dealing_range == b.dealing_range
        if a.equilibrium is not None:
            assert a.equilibrium.midpoint == b.equilibrium.midpoint
            assert a.equilibrium.provenance.evidence_id != b.equilibrium.provenance.evidence_id
