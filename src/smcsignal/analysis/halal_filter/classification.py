"""Deterministic registry lookup; unknown is never silently treated as HALAL."""

from __future__ import annotations

from enum import StrEnum

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.halal_filter.config import FilterMode, HalalFilterConfig, normalize_asset


class AssetClassification(StrEnum):
    HALAL = "HALAL"
    HARAM = "HARAM"
    UNKNOWN = "UNKNOWN"


def classify_asset(symbol: str, config: HalalFilterConfig | None = None) -> AssetClassification:
    settings = HalalFilterConfig() if config is None else config
    if not isinstance(settings, HalalFilterConfig):
        raise AnalysisConfigurationError("config must be HalalFilterConfig")
    key = normalize_asset(symbol, error=AnalysisInputError)
    if settings.mode is FilterMode.ALLOW_LIST:
        if key in settings.allowed_assets:
            return AssetClassification.HALAL
        return AssetClassification.UNKNOWN
    if key in settings.denied_assets:
        return AssetClassification.HARAM
    return AssetClassification.UNKNOWN


def decision_reason(symbol: str, config: HalalFilterConfig) -> str:
    key = normalize_asset(symbol, error=AnalysisInputError)
    if config.mode is FilterMode.ALLOW_LIST:
        if key in config.allowed_assets:
            return "listed_in_allow_list"
        return "absent_from_allow_list"
    if key in config.denied_assets:
        return "listed_in_deny_list"
    return "absent_from_deny_list"


def is_eligible(classification: AssetClassification) -> bool:
    return classification is AssetClassification.HALAL
