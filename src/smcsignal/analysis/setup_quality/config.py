"""Strict sqs-v1 publication threshold; weights are frozen methodology, not TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "sqs-v1"
DEFAULT_PUBLISH_THRESHOLD = 75
MAX_SCORE = 100


@dataclass(frozen=True, slots=True)
class SetupQualityConfig:
    """Only the integer publish threshold is configurable. Component weights are not."""

    publish_threshold: int = DEFAULT_PUBLISH_THRESHOLD

    def __post_init__(self) -> None:
        value = self.publish_threshold
        if type(value) is not int or not 0 <= value <= MAX_SCORE:
            raise AnalysisConfigurationError("publish_threshold must be an integer from 0 to 100")


def load_setup_quality_config(path: str | Path) -> SetupQualityConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("setup_quality")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load setup quality configuration: {exc}") from exc
    names = {field.name for field in fields(SetupQualityConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[setup_quality] must contain exactly: " + ", ".join(sorted(names))
        )
    return SetupQualityConfig(**table)
