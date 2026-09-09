"""Deterministic configuration artifact and report identity for performance."""

from __future__ import annotations

from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.performance.config import (
    METHODOLOGY_VERSION,
    PerformanceConfig,
)
from smcsignal.analysis.setup_attribution.config import SetupAttributionConfig


def configuration_artifact(config: PerformanceConfig) -> bytes:
    """Frozen methodology artifact; the descriptive-only role is declared."""

    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "role": "descriptive_statistics_only",
            "reclassification": False,
            "optimization": False,
            "expected_return_forecast": False,
            "advice": False,
            "execution": False,
            "open_and_finalized_separated": True,
        }
    )


def report_identity(
    settings: PerformanceConfig,
    outcome_settings: OutcomeTrackingConfig,
    attribution_settings: SetupAttributionConfig | None,
    series_payload: object,
) -> str:
    """Stable identity from the configuration and every consumed record."""

    payload = {
        "methodology": METHODOLOGY_VERSION,
        "settings": settings,
        "outcome_settings": outcome_settings,
        "attribution_settings": attribution_settings,
        "series": series_payload,
    }
    return f"performance-report:{digest(payload)}"
