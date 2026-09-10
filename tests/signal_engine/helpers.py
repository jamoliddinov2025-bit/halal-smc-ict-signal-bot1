"""Reuse existing eligibility frames; the engine does not rerun earlier detectors."""

from datetime import timedelta

from smcsignal.analysis.signal_engine import (
    Signal,
    SignalDirection,
    SignalEngineAnalyzer,
    SignalEngineConfig,
    SignalStatus,
    analyze_signal_engine,
)
from smcsignal.analysis.signal_engine.models import current_observation
from tests.signal_eligibility.helpers import run as eligibility_run


def matching_config(frame) -> SignalEngineConfig:
    return SignalEngineConfig(publish_threshold=frame.decision.eligibility.publish_threshold)


def run(frames=None, config=None, *, sqs_config=None):
    upstream = frames if frames is not None else eligibility_run(sqs_config=sqs_config)
    if config is None and upstream:
        config = matching_config(upstream[0])
    elif config is None:
        config = SignalEngineConfig()
    return analyze_signal_engine(upstream, config)


def analyzer(config=None):
    return SignalEngineAnalyzer(config if config is not None else SignalEngineConfig())


def make_signal(frame, status: SignalStatus, direction: SignalDirection, **overrides) -> Signal:
    observation = current_observation(frame)
    eligibility = frame.decision.eligibility
    values = {
        "status": status,
        "direction": direction,
        "classification": eligibility.classification,
        "eligibility_status": eligibility.status,
        "bias": eligibility.bias,
        "score_total": eligibility.score_total,
        "threshold_passed": eligibility.threshold_passed,
        "publish_threshold": eligibility.publish_threshold,
        "symbol": observation.reference.series.symbol,
        "timeframe": observation.reference.series.timeframe,
        "setup_identity": "spot-setup:test",
        "candle": observation.reference,
        "candle_opened_at": observation.reference.opened_at,
        "candle_closed_at": observation.reference.closed_at,
        "evidence_available_at": observation.available_at,
        "eligibility_available_at": frame.provenance.available_at,
        "published_at": observation.available_at,
    }
    values.update(overrides)
    return Signal(**values)


def earlier(frame, delta=timedelta(seconds=1)):
    return current_observation(frame).available_at - delta
