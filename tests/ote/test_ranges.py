from smcsignal.analysis.ote import OTEClassification
from tests.analysis.helpers import bar, series
from tests.ote.helpers import golden, run
from tests.premium_discount.helpers import golden as pd_series


def test_missing_opposing_pair_is_explicit_insufficient_context():
    frames = run(golden())
    assert all(f.zone is None for f in frames[:4])
    assert all(f.classification == OTEClassification.INSUFFICIENT_CONTEXT for f in frames[:5])
    assert frames[2].upstream.latest_high is None or frames[2].upstream.dealing_range is None


def test_same_pivot_pair_does_not_fall_back_to_an_older_ote_zone():
    candles = (*pd_series()[:5], bar(5, "13.5", high=20, low=5), bar(6, 12, high=14, low=10))
    frames = run(candles)
    assert frames[4].upstream.dealing_range is not None
    assert frames[6].upstream.dealing_range is None
    assert frames[6].zone is None
    assert frames[6].classification == OTEClassification.INSUFFICIENT_CONTEXT


def test_nonpositive_latest_pair_is_insufficient_without_swapping():
    frames = run(series([20, 25, 20, 23, 17, 12, 12, 13, 11]))
    assert frames[4].upstream.dealing_range is not None
    assert frames[8].upstream.dealing_range is None
    assert frames[8].zone is None
    assert frames[8].classification == OTEClassification.INSUFFICIENT_CONTEXT


def test_reused_phase8_range_reuses_the_exact_ote_zone_object():
    frames = run(golden())
    assert frames[5].zone is frames[4].zone
    assert frames[9].zone is frames[4].zone
    assert frames[5].zone.range_id == frames[4].upstream.dealing_range.range_id


def test_new_phase8_range_creates_an_independent_zone_without_merging():
    frames = run(pd_series())
    created = [f.zone for f in frames if f.zone is not None]
    ids = [z.range_id for z in created]
    assert len(ids) >= 2
    assert ids[0] != ids[-1]
    assert all(f.classification == OTEClassification.INSUFFICIENT_CONTEXT for f in frames)


def test_later_range_does_not_rewrite_the_earlier_zone_identity():
    frames = run((*golden(), bar(10, 50), bar(11, 30)))
    earlier = frames[5].zone
    later = frames[11].zone
    assert earlier is frames[4].zone
    assert later is not None and later.range_id != earlier.range_id
    assert earlier.lower_boundary == frames[5].zone.lower_boundary


def test_single_confirmed_low_without_high_is_insufficient():
    frame = run(series([15, 10, 12]))[2]
    assert frame.upstream.latest_low is not None
    assert frame.upstream.latest_high is None
    assert frame.classification == OTEClassification.INSUFFICIENT_CONTEXT
    assert frame.zone is None
