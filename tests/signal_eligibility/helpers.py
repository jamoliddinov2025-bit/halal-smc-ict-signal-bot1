"""Reuse existing SQS/Halal fixtures; eligibility does not rerun earlier detectors."""

from smcsignal.analysis.signal_eligibility import (
    SignalEligibilityAnalyzer,
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)
from tests.setup_quality.helpers import run as sqs_run


def run(frames=None, config=None, *, sqs_config=None):
    return analyze_signal_eligibility(
        frames if frames is not None else sqs_run(config=sqs_config),
        config if config is not None else SignalEligibilityConfig(),
    )


def analyzer(config=None):
    return SignalEligibilityAnalyzer(config if config is not None else SignalEligibilityConfig())
