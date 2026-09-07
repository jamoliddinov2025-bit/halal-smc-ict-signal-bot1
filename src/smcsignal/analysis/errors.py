"""Typed failures for analysis configuration, inputs, and causal metadata."""


class AnalysisError(Exception):
    """Base exception for Phase 3 analysis."""


class AnalysisConfigurationError(AnalysisError, ValueError):
    """Analysis configuration is invalid or unsupported."""


class AnalysisInputError(AnalysisError, ValueError):
    """Input violates the closed, chronological candle/confirmation contract."""
