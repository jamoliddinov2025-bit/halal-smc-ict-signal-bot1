from smcsignal.analysis.halal_filter import AssetClassification, FilterMode, HalalFilterConfig
from smcsignal.analysis.mtf import MTFDirection
from smcsignal.analysis.ote import OTEClassification
from smcsignal.analysis.premium_discount import PDClassification
from smcsignal.analysis.setup_quality import (
    WEIGHTS,
    ScoreComponent,
    SetupQualityConfig,
    analyze_setup_quality,
)
from smcsignal.analysis.setup_quality.calculation import (
    MTF_POINTS,
    OTE_POINTS,
    PD_POINTS,
    _presence,
    breakdown_for,
    nested_mtf,
    nested_ote,
    nested_pd,
)
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.setup_quality.helpers import run


def test_hand_computed_index_zero_is_halal_only():
    frame = run()[0]
    assert frame.upstream.classification is AssetClassification.HALAL
    assert frame.total == 10
    assert frame.score.breakdown.halal == 10
    assert frame.score.breakdown.mtf == 0
    assert frame.score.breakdown.mss == 0
    assert frame.score.breakdown.displacement == 0
    assert frame.score.breakdown.liquidity_sweep == 0
    assert frame.score.breakdown.fvg == 0
    assert frame.score.breakdown.order_block == 0
    assert frame.score.breakdown.breaker_block == 0
    assert frame.score.breakdown.mitigation_block == 0
    assert frame.score.breakdown.premium_discount == 0
    assert frame.score.breakdown.ote == 0
    assert frame.threshold_passed is False
    assert nested_mtf(frame.upstream).direction is MTFDirection.INSUFFICIENT_CONTEXT
    assert nested_pd(frame.upstream).classification is PDClassification.INSUFFICIENT_CONTEXT
    assert nested_ote(frame.upstream).classification is OTEClassification.INSUFFICIENT_CONTEXT


def test_hand_computed_index_four_adds_directional_mtf():
    frame = run()[4]
    assert frame.total == 25
    assert frame.score.breakdown.halal == 10
    assert frame.score.breakdown.mtf == 15
    assert nested_mtf(frame.upstream).direction is MTFDirection.BULLISH
    assert frame.threshold_passed is False
    assert frame.score.breakdown.reasons[1] == "mtf_directional"


def test_default_threshold_rejects_the_entire_synthetic_series():
    frames = run()
    assert [frame.total for frame in frames] == [10, 10, 10, 10] + [25] * 13
    assert all(frame.threshold_passed is False for frame in frames)
    assert all(0 <= frame.total <= 100 for frame in frames)
    assert all(frame.total == frame.score.breakdown.total for frame in frames)


def test_unknown_asset_is_hard_gated_to_zero():
    frames = run(halal_run(mtf_for("ADAUSDT")))
    assert all(frame.upstream.classification is AssetClassification.UNKNOWN for frame in frames)
    assert all(frame.total == 0 for frame in frames)
    assert all(frame.score.breakdown.halal == 0 for frame in frames)
    assert all(frame.threshold_passed is False for frame in frames)
    assert all(reason == "gated_unknown" for reason in frames[4].score.breakdown.reasons)


def test_haram_asset_is_hard_gated_to_zero_even_with_directional_mtf():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )
    frames = run(halal_run(mtf_for("XYZUSDT"), config))
    assert frames[4].upstream.classification is AssetClassification.HARAM
    assert nested_mtf(frames[4].upstream).direction is MTFDirection.BULLISH
    assert frames[4].total == 0
    assert frames[4].threshold_passed is False
    assert all(reason == "gated_haram" for reason in frames[4].score.breakdown.reasons)


def test_unknown_never_passes_even_when_the_threshold_is_zero():
    frames = analyze_setup_quality(
        halal_run(mtf_for("ADAUSDT")), SetupQualityConfig(publish_threshold=0)
    )
    assert all(frame.total == 0 for frame in frames)
    assert all(frame.eligible is False for frame in frames)
    assert all(frame.threshold_passed is False for frame in frames)


def test_halal_passes_a_threshold_at_or_below_the_integer_total():
    low = analyze_setup_quality(halal_run(), SetupQualityConfig(11))
    assert low[0].total == 10 and low[0].threshold_passed is False
    assert low[4].total == 25 and low[4].threshold_passed is True
    zero = analyze_setup_quality(halal_run(), SetupQualityConfig(0))
    assert all(frame.threshold_passed is True for frame in zero)
    exact = analyze_setup_quality(halal_run(), SetupQualityConfig(25))
    assert exact[0].threshold_passed is False
    assert exact[4].threshold_passed is True


def test_unjoined_parallel_consumers_contribute_zero():
    frame = run()[4]
    assert frame.score.breakdown.mss == 0
    assert frame.score.breakdown.breaker_block == 0
    assert frame.score.breakdown.mitigation_block == 0
    assert frame.total == 25
    assert frame.total <= 80


def test_missing_current_candle_events_are_zero_points_not_failure():
    breakdown = breakdown_for(halal_run()[4])
    assert breakdown.displacement == 0
    assert breakdown.liquidity_sweep == 0
    assert breakdown.fvg == 0
    assert breakdown.order_block == 0
    assert "missing_evidence" in breakdown.reasons
    assert breakdown.total == 25


def test_presence_awards_full_weight_or_zero():
    assert _presence((), 12, "displacement_present") == (0, "missing_evidence")
    assert _presence((object(),), 12, "displacement_present") == (12, "displacement_present")
    assert _presence((object(), object()), 8, "fvg_present") == (8, "fvg_present")


def test_partial_context_tables_are_integers_inside_each_weight():
    for label, points in MTF_POINTS.items():
        assert type(points) is int
        assert 0 <= points <= WEIGHTS[ScoreComponent.MTF]
        assert label in MTFDirection
    for _label, points in PD_POINTS.items():
        assert type(points) is int
        assert 0 <= points <= WEIGHTS[ScoreComponent.PREMIUM_DISCOUNT]
    for _label, points in OTE_POINTS.items():
        assert type(points) is int
        assert 0 <= points <= WEIGHTS[ScoreComponent.OTE]
    assert MTF_POINTS[MTFDirection.BULLISH] == MTF_POINTS[MTFDirection.BEARISH] == 15
    assert MTF_POINTS[MTFDirection.NEUTRAL] == 8
    assert MTF_POINTS[MTFDirection.MIXED] == 5
    assert PD_POINTS[PDClassification.DISCOUNT] == 8
    assert PD_POINTS[PDClassification.EQUILIBRIUM] == 5
    assert PD_POINTS[PDClassification.PREMIUM] == 3
    assert PD_POINTS[PDClassification.OUTSIDE_RANGE] == 1
    assert OTE_POINTS[OTEClassification.INSIDE_OTE] == 7
    assert OTE_POINTS[OTEClassification.BELOW_OTE] == OTE_POINTS[OTEClassification.ABOVE_OTE] == 3


def test_threshold_passed_is_not_a_trade_signal():
    frame = run()[4]
    assert frame.threshold_passed is False
    assert not hasattr(frame, "signal")
    assert not hasattr(frame.score, "side")
    assert not hasattr(frame.score, "probability")
