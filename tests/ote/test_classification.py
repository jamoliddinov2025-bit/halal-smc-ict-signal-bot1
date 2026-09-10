from decimal import Decimal

import pytest

from smcsignal.analysis.ote import OTEClassification
from tests.analysis.helpers import bar
from tests.ote.helpers import (
    BULLISH_OTE_LOWER,
    BULLISH_OTE_UPPER,
    classified,
    golden,
    run,
)


def test_documented_sequence_covers_all_four_classifications():
    frames = run(golden())
    assert [f.classification for f in frames] == [
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSUFFICIENT_CONTEXT,
        OTEClassification.INSIDE_OTE,
        OTEClassification.BELOW_OTE,
        OTEClassification.ABOVE_OTE,
        OTEClassification.INSIDE_OTE,
        OTEClassification.INSIDE_OTE,
    ]
    assert all(f.evaluated_price == f.observation.evaluation.candle.close for f in frames)
    assert all(f.classification == f.observation.classification for f in frames)


@pytest.mark.parametrize(
    "close,expected",
    [
        ("15.719999", OTEClassification.BELOW_OTE),
        ("15.72", OTEClassification.INSIDE_OTE),
        ("18", OTEClassification.INSIDE_OTE),
        ("21.16", OTEClassification.INSIDE_OTE),
        ("21.160001", OTEClassification.ABOVE_OTE),
        ("12", OTEClassification.BELOW_OTE),
        ("25", OTEClassification.ABOVE_OTE),
        ("9", OTEClassification.BELOW_OTE),
        ("41", OTEClassification.ABOVE_OTE),
    ],
)
def test_inclusive_ote_boundaries_and_outside_range_closes(close, expected):
    price = Decimal(str(close))
    frame = run(classified(close, high=max(price, Decimal(30)), low=min(price, Decimal(10))))[5]
    assert (frame.zone.lower_boundary, frame.zone.upper_boundary) == (
        BULLISH_OTE_LOWER,
        BULLISH_OTE_UPPER,
    )
    assert frame.classification == expected
    assert frame.evaluated_price == Decimal(str(close))


def test_wick_inside_ote_does_not_classify_the_close():
    frame = run((*golden()[:5], bar(5, 12, high=18, low=10)))[5]
    assert frame.observation.evaluation.candle.high == 18
    assert BULLISH_OTE_LOWER < 18 < BULLISH_OTE_UPPER
    assert frame.classification == OTEClassification.BELOW_OTE


def test_open_inside_ote_does_not_override_close_basis():
    frame = run((*golden()[:5], bar(5, 12, opening=18, high=18, low=10)))[5]
    assert frame.observation.evaluation.candle.open == 18
    assert frame.classification == OTEClassification.BELOW_OTE


def test_doji_close_inside_is_inside():
    frame = run((*golden()[:5], bar(5, 18, opening=18, high=18, low=18)))[5]
    assert frame.classification == OTEClassification.INSIDE_OTE


def test_confirmation_candle_is_insufficient_even_when_close_sits_in_the_future_zone():
    frame = run((*golden()[:4], bar(4, 18, high=26, low=17)))[4]
    assert frame.zone is not None
    assert frame.zone.lower_boundary <= frame.evaluated_price <= frame.zone.upper_boundary
    assert frame.classification == OTEClassification.INSUFFICIENT_CONTEXT
    assert frame.observation.zone is None
