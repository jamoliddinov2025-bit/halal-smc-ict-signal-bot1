from smcsignal.analysis import TrendDirection
from smcsignal.analysis.ote import OTEClassification, OTEDirection
from tests.analysis.helpers import mirrored
from tests.ote.helpers import bearish_stable, golden, run


def test_bullish_range_maps_to_bullish_ote_direction():
    frame = run(golden())[5]
    assert frame.zone.dealing_range.direction == TrendDirection.BULLISH
    assert frame.zone.direction == OTEDirection.BULLISH
    assert frame.observation.direction == OTEDirection.BULLISH


def test_bearish_range_maps_to_bearish_ote_direction():
    frames = run(bearish_stable())
    assert frames[3].zone.direction == OTEDirection.BEARISH
    assert frames[3].classification == OTEClassification.INSUFFICIENT_CONTEXT
    assert frames[4].zone is frames[3].zone
    assert frames[4].observation.direction == OTEDirection.BEARISH
    assert frames[4].classification == OTEClassification.INSIDE_OTE


def test_mirror_reverses_ote_direction_and_preserves_ordered_bounds():
    original = run(golden())
    inverse = run(mirrored(golden()))
    for a, b in zip(original, inverse, strict=True):
        assert (a.zone is None) == (b.zone is None)
        if a.zone is not None:
            assert a.zone.direction != b.zone.direction
            assert a.zone.lower_boundary + b.zone.upper_boundary == 1000
            assert a.zone.upper_boundary + b.zone.lower_boundary == 1000
        assert (a.classification == OTEClassification.INSUFFICIENT_CONTEXT) == (
            b.classification == OTEClassification.INSUFFICIENT_CONTEXT
        )
        if a.classification == OTEClassification.INSIDE_OTE:
            assert b.classification == OTEClassification.INSIDE_OTE
        elif a.classification == OTEClassification.BELOW_OTE:
            assert b.classification == OTEClassification.ABOVE_OTE
        elif a.classification == OTEClassification.ABOVE_OTE:
            assert b.classification == OTEClassification.BELOW_OTE


def test_insufficient_observation_has_no_direction_label():
    frame = run(golden())[2]
    assert frame.zone is None
    assert frame.observation.direction is None
    assert frame.observation.zone_id is None
