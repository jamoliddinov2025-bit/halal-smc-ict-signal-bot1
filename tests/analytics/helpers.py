"""Phase 26A tests drive the real Phase 3-17 chain; analytics reruns nothing.

The helpers stop the standard Phase 17 chain *before* the signal engine so the
tests can drive the real ``SignalEngineAnalyzer`` (bare or observed) frame by
frame over real eligibility facts. Candle fixtures reuse the audited Phase 18
synthetic histories, whose BUY publications and evaluations are hand-checked in
the existing outcome-tracking suite.
"""

from smcsignal.analysis.halal_filter import HalalFilterConfig, analyze_halal
from smcsignal.analysis.outcome_tracking import OutcomeTrackingAnalyzer, OutcomeTrackingConfig
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_engine import SignalEngineAnalyzer, SignalStatus
from smcsignal.analytics import AnalyticsObserver, ObservedSignalEngine
from tests.mtf.helpers import ote_frames
from tests.mtf.helpers import run as mtf_run
from tests.outcome_tracking.helpers import candles_for
from tests.signal_engine.helpers import matching_config


def eligibility_chain(candles=None, *, threshold: int = 10):
    """Real Phase 3-16 chain over the audited synthetic history."""

    primary = ote_frames(candles if candles is not None else candles_for(), "15m")
    halal = analyze_halal(mtf_run(primary=primary), HalalFilterConfig())
    scored = analyze_setup_quality(halal, SetupQualityConfig(threshold))
    return analyze_signal_eligibility(scored, SignalEligibilityConfig())


def real_engine(eligible=None) -> SignalEngineAnalyzer:
    """The real Phase 17 engine configured for the given eligibility frames."""

    frames = eligible if eligible is not None else eligibility_chain()
    return SignalEngineAnalyzer(matching_config(frames[0]))


def observed_engine(observer: AnalyticsObserver | None = None, candles=None, *, threshold=10):
    """The real engine composed with the Phase 26A observer (the adapter)."""

    eligible = eligibility_chain(candles, threshold=threshold)
    observer = observer if observer is not None else AnalyticsObserver()
    return ObservedSignalEngine(observer, real_engine(eligible)), eligible


def publish(adapter_or_engine, eligible) -> tuple:
    """Push real eligibility frames through a real engine, in order."""

    return tuple(adapter_or_engine.update(frame) for frame in eligible)


def buy_frames(frames) -> tuple:
    return tuple(frame for frame in frames if frame.status is SignalStatus.BUY_SIGNAL)


def manual_bridge(frames, config=None):
    """The Phase 26A hand-rolled bridge (reference path A for equivalence).

    Drives the observer and a separate Phase 18 evaluator over the same frames
    and forwards completed finals by hand — exactly what Phase 26A tests did
    before Phase 26B composed the loop.
    """

    settings = config if config is not None else OutcomeTrackingConfig()
    observer = AnalyticsObserver(settings)
    tracker = OutcomeTrackingAnalyzer(settings)
    steps: list = []
    for frame in frames:
        observation = observer.observe(frame)
        snapshot = tracker.update(frame)
        for record in snapshot.completed:
            observer.record_finalized(record)
        steps.append((observation, snapshot))
    return observer, tracker, tuple(steps)


__all__ = [
    "buy_frames",
    "candles_for",
    "eligibility_chain",
    "manual_bridge",
    "observed_engine",
    "publish",
    "real_engine",
]
