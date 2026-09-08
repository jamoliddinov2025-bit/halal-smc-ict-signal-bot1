from smcsignal.analysis.halal_filter import AssetClassification, FilterMode, HalalFilterConfig
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.mtf import MTFDirection
from smcsignal.analysis.ote import OTEClassification, OTEDirection
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    EligibilityReason,
    EligibilityStatus,
    MarketBias,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_eligibility.calculation import (
    collect_votes,
    resolve_bias,
    vote_mtf,
    vote_order_block,
    vote_ote,
    vote_structure_event,
)
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.signal_eligibility.helpers import run


def test_hand_computed_index_zero_is_neutral_and_not_eligible():
    frame = run()[0]
    assert frame.status is EligibilityStatus.NOT_ELIGIBLE
    assert frame.bias is MarketBias.NEUTRAL
    assert frame.eligible is False
    assert frame.decision.eligibility.classification is AssetClassification.HALAL
    assert frame.decision.eligibility.score_total == 10
    assert EligibilityReason.THRESHOLD_NOT_MET in frame.decision.reasons
    assert EligibilityReason.NO_DIRECTIONAL_EVIDENCE in frame.decision.reasons
    assert EligibilityReason.MTF_INSUFFICIENT in frame.decision.reasons
    assert EligibilityReason.MSS_MISSING in frame.decision.reasons
    assert EligibilityReason.BREAKER_MISSING in frame.decision.reasons
    assert EligibilityReason.MITIGATION_MISSING in frame.decision.reasons


def test_hand_computed_index_four_is_long_bias_below_default_threshold():
    frame = run()[4]
    assert frame.status is EligibilityStatus.NOT_ELIGIBLE
    assert frame.bias is MarketBias.LONG_BIAS
    assert frame.eligible is False
    assert frame.decision.eligibility.score_total == 25
    assert EligibilityReason.THRESHOLD_NOT_MET in frame.decision.reasons
    assert EligibilityReason.MTF_LONG in frame.decision.reasons
    assert EligibilityReason.CONFLICTING_EVIDENCE not in frame.decision.reasons


def test_halal_and_threshold_marks_eligible_without_becoming_a_trade():
    frames = run(sqs_config=SetupQualityConfig(10))
    assert frames[0].status is EligibilityStatus.ELIGIBLE
    assert frames[0].bias is MarketBias.NEUTRAL
    assert frames[0].eligible is True
    assert EligibilityReason.HALAL_AND_THRESHOLD in frames[0].decision.reasons
    assert frames[4].status is EligibilityStatus.ELIGIBLE
    assert frames[4].bias is MarketBias.LONG_BIAS
    assert not hasattr(frames[4], "side")
    assert not hasattr(frames[4].decision, "entry")


def test_unknown_asset_is_hard_gated():
    frames = analyze_signal_eligibility(analyze_setup_quality(halal_run(mtf_for("ADAUSDT"))))
    assert all(frame.status is EligibilityStatus.NOT_ELIGIBLE for frame in frames)
    assert all(frame.bias is MarketBias.NEUTRAL for frame in frames)
    assert all(frame.decision.reasons == (EligibilityReason.GATED_UNKNOWN,) for frame in frames)


def test_haram_asset_is_hard_gated_even_with_directional_mtf():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )
    frames = analyze_signal_eligibility(
        analyze_setup_quality(halal_run(mtf_for("XYZUSDT"), config))
    )
    assert frames[4].decision.eligibility.classification is AssetClassification.HARAM
    assert frames[4].status is EligibilityStatus.NOT_ELIGIBLE
    assert frames[4].bias is MarketBias.NEUTRAL
    assert frames[4].decision.reasons == (EligibilityReason.GATED_HARAM,)


def test_unknown_never_becomes_eligible_when_the_score_threshold_is_zero():
    frames = analyze_signal_eligibility(
        analyze_setup_quality(halal_run(mtf_for("ADAUSDT")), SetupQualityConfig(0))
    )
    assert all(frame.eligible is False for frame in frames)
    assert all(frame.bias is MarketBias.NEUTRAL for frame in frames)


def test_mtf_votes_map_to_bias_or_abstain():
    assert vote_mtf(MTFDirection.BULLISH).bias is MarketBias.LONG_BIAS
    assert vote_mtf(MTFDirection.BEARISH).bias is MarketBias.SHORT_BIAS
    assert vote_mtf(MTFDirection.MIXED).mixed is True
    assert vote_mtf(MTFDirection.MIXED).bias is None
    assert vote_mtf(MTFDirection.NEUTRAL).bias is None
    assert vote_mtf(MTFDirection.INSUFFICIENT_CONTEXT).bias is None


def test_structure_order_block_and_ote_votes_cover_short_bias():
    assert vote_structure_event(TrendDirection.BEARISH).bias is MarketBias.SHORT_BIAS
    assert vote_structure_event(TrendDirection.BULLISH).bias is MarketBias.LONG_BIAS
    assert vote_order_block(TrendDirection.BEARISH).bias is MarketBias.SHORT_BIAS
    assert vote_order_block(None).reason is EligibilityReason.ORDER_BLOCK_MISSING
    assert (
        vote_ote(OTEClassification.INSIDE_OTE, OTEDirection.BEARISH).bias is MarketBias.SHORT_BIAS
    )
    assert vote_ote(OTEClassification.INSIDE_OTE, OTEDirection.BULLISH).bias is MarketBias.LONG_BIAS
    assert vote_ote(OTEClassification.BELOW_OTE, OTEDirection.BULLISH).bias is None


def test_conflicting_votes_resolve_to_neutral():
    long = vote_mtf(MTFDirection.BULLISH)
    short = vote_order_block(TrendDirection.BEARISH)
    assert resolve_bias((long, short)) is MarketBias.NEUTRAL
    assert resolve_bias((vote_mtf(MTFDirection.MIXED),)) is MarketBias.NEUTRAL
    assert resolve_bias((vote_mtf(MTFDirection.BEARISH),)) is MarketBias.SHORT_BIAS
    assert resolve_bias((vote_mtf(MTFDirection.BULLISH),)) is MarketBias.LONG_BIAS
    assert resolve_bias(()) is MarketBias.NEUTRAL


def test_collected_votes_on_the_synthetic_series_are_mtf_only():
    frames = run()
    early = collect_votes(frames[0].upstream)
    later = collect_votes(frames[4].upstream)
    assert resolve_bias(early) is MarketBias.NEUTRAL
    assert resolve_bias(later) is MarketBias.LONG_BIAS
    assert later[0].reason is EligibilityReason.MTF_LONG
    assert {item.source for item in later} >= {
        "mtf",
        "structure",
        "order_block",
        "ote",
        "mss",
        "breaker",
        "mitigation",
    }
