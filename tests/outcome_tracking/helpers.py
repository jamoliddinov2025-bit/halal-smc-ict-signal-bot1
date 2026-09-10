"""Reuse existing Phase 17 signal fixtures; outcomes do not rerun earlier detectors."""

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from smcsignal.analysis.halal_filter import HalalFilterConfig, analyze_halal
from smcsignal.analysis.outcome_tracking import (
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_engine import SignalStatus, analyze_signal_engine
from tests.mtf.helpers import EIGHT, bar_at, bars, ote_frames
from tests.mtf.helpers import run as mtf_run
from tests.signal_engine.helpers import matching_config

RISING = tuple(range(20, 37))
FALLING_TAIL = (*range(20, 25), 23, 22, 21, 20, 19, 18, 17, 16, 15, 14)
FLAT_TAIL = (*range(20, 25), 23, 22, 25, 24, 23, 26, 25, 24, 23, 24)
STEP = timedelta(minutes=15)


def candles_for(prices=RISING):
    return bars("15m", prices, start=EIGHT)


def tie_tail_candles():
    """Equal highs and lows after the index-4 BUY; first occurrences must survive."""

    head = bars("15m", range(20, 25), start=EIGHT)
    return (
        *head,
        bar_at(EIGHT + 5 * STEP, 23, high=25, low=22),
        bar_at(EIGHT + 6 * STEP, 24, high=25, low=21),
        bar_at(EIGHT + 7 * STEP, 23, high=24, low=21),
    )


def signal_frames(candles=None, *, threshold=10):
    """Full Phase 3-17 chain; BUY facts at index i depend only on candles 0..i."""

    primary = ote_frames(candles if candles is not None else candles_for(), "15m")
    halal = analyze_halal(mtf_run(primary=primary), HalalFilterConfig())
    scored = analyze_setup_quality(halal, SetupQualityConfig(threshold))
    eligible = analyze_signal_eligibility(scored, SignalEligibilityConfig())
    return analyze_signal_engine(eligible, matching_config(eligible[0]))


def run(frames=None, config=None, *, candles=None, threshold=10):
    return analyze_outcome_tracking(
        frames if frames is not None else signal_frames(candles, threshold=threshold),
        config if config is not None else OutcomeTrackingConfig(),
    )


def analyzer(config=None):
    return OutcomeTrackingAnalyzer(config if config is not None else OutcomeTrackingConfig())


def buy_indices(frames):
    return [index for index, frame in enumerate(frames) if frame.status is SignalStatus.BUY_SIGNAL]


def created_records(snapshots):
    return [record for snapshot in snapshots for record in snapshot.created]


def completed_records(snapshots):
    return [record for snapshot in snapshots for record in snapshot.completed]


def outcome_for(snapshots, signal_id):
    versions = [
        record
        for snapshot in snapshots
        for record in (*snapshot.created, *snapshot.evaluated)
        if record.signal_id == signal_id
    ]
    assert versions, "expected at least one version for the signal"
    return versions


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Independent 50-significant-digit half-even reference for descriptive ratios."""

    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return numerator / denominator


__all__ = [
    "FALLING_TAIL",
    "FLAT_TAIL",
    "RISING",
    "STEP",
    "analyzer",
    "bars",
    "buy_indices",
    "candles_for",
    "completed_records",
    "created_records",
    "outcome_for",
    "ratio",
    "run",
    "signal_frames",
    "tie_tail_candles",
]
