from smcsignal.analysis.halal_filter import AssetClassification
from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_eligibility import EligibilityStatus, MarketBias
from smcsignal.analysis.signal_engine import SignalDirection, SignalReason, SignalStatus
from tests.signal_engine.helpers import run


def test_buy_signal_retains_upstream_publication_metadata_and_present_reasons():
    frame = run(sqs_config=SetupQualityConfig(10))[4]
    item = frame.candidate.signal
    assert frame.status is SignalStatus.BUY_SIGNAL
    assert frame.direction is SignalDirection.LONG
    assert frame.signal_id == frame.candidate.signal_id == frame.candidate.provenance.evidence_id
    assert frame.setup_identity == item.setup_identity
    assert item.symbol == frame.upstream.provenance.series.symbol
    assert item.timeframe == frame.upstream.provenance.series.timeframe
    assert item.classification is AssetClassification.HALAL
    assert item.eligibility_status is EligibilityStatus.ELIGIBLE
    assert item.bias is MarketBias.LONG_BIAS
    assert item.score_total == frame.upstream.decision.eligibility.score_total
    assert item.publish_threshold == frame.settings.publish_threshold
    assert item.threshold_passed is True
    assert item.candle == frame.candidate.provenance.source_candles[0]
    assert frame.upstream.provenance.as_reference() in frame.candidate.evidence
    assert SignalReason.HALAL_ASSET in frame.candidate.reasons
    assert SignalReason.SQS_THRESHOLD_PASSED in frame.candidate.reasons
    assert SignalReason.LONG_BIAS in frame.candidate.reasons
    assert SignalReason.BULLISH_MSS not in frame.candidate.reasons
    assert SignalReason.DUPLICATE_SETUP not in frame.candidate.reasons
    assert "entry" not in frame.__dataclass_fields__
    assert "stop_loss" not in item.__dataclass_fields__


def test_rejection_reasons_do_not_claim_absent_confluence():
    below = run()[4]
    assert below.status is SignalStatus.NO_SIGNAL
    assert SignalReason.SQS_BELOW_THRESHOLD in below.candidate.reasons
    assert SignalReason.HALAL_ASSET not in below.candidate.reasons
    assert SignalReason.LONG_BIAS not in below.candidate.reasons
    early = run(sqs_config=SetupQualityConfig(10))[0]
    assert SignalReason.NEUTRAL_BIAS in early.candidate.reasons
    assert SignalReason.LONG_BIAS not in early.candidate.reasons
    duplicate = run(sqs_config=SetupQualityConfig(10))[5]
    assert duplicate.candidate.reasons == (SignalReason.DUPLICATE_SETUP,)


def test_published_stream_never_contains_sell_or_short():
    frames = run(sqs_config=SetupQualityConfig(10))
    assert {frame.status for frame in frames} <= {
        SignalStatus.BUY_SIGNAL,
        SignalStatus.BEARISH_AVOID,
        SignalStatus.NO_SIGNAL,
    }
    assert {frame.direction for frame in frames} <= {SignalDirection.LONG, SignalDirection.NONE}
    assert all(
        frame.direction is SignalDirection.LONG or frame.status is not SignalStatus.BUY_SIGNAL
        for frame in frames
    )
    assert "SELL" not in {item.value for item in SignalStatus}
    assert "SHORT" not in {item.value for item in SignalDirection}
