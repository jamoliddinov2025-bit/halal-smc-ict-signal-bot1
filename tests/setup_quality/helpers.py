"""Reuse existing Halal/MTF fixtures; SQS does not rerun earlier detectors."""

from smcsignal.analysis.setup_quality import (
    SetupQualityAnalyzer,
    SetupQualityConfig,
    analyze_setup_quality,
)
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run


def run(frames=None, config=None):
    return analyze_setup_quality(
        frames if frames is not None else halal_run(),
        config if config is not None else SetupQualityConfig(),
    )


def analyzer(config=None):
    return SetupQualityAnalyzer(config if config is not None else SetupQualityConfig())


__all__ = ["analyzer", "halal_run", "mtf_for", "run"]
