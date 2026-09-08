"""Strict signal-engine-v1 invariants; not execution, ranking, or a second score."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.analysis.setup_quality.config import DEFAULT_PUBLISH_THRESHOLD, MAX_SCORE

METHODOLOGY_VERSION = "signal-engine-v1"


class DuplicatePolicy(StrEnum):
    ONE_PER_SETUP = "one_per_setup"


@dataclass(frozen=True, slots=True)
class SignalEngineConfig:
    """Declare frozen spot-only publication invariants. No entries or sides are configured."""

    enabled: bool = True
    publish_threshold: int = DEFAULT_PUBLISH_THRESHOLD
    spot_only: bool = True
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.ONE_PER_SETUP

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict signal-engine-v1")
        if type(self.publish_threshold) is not int or not 0 <= self.publish_threshold <= MAX_SCORE:
            raise AnalysisConfigurationError("publish_threshold must be an integer from 0 to 100")
        if type(self.spot_only) is not bool or not self.spot_only:
            raise AnalysisConfigurationError("spot_only must be true in strict signal-engine-v1")
        if not isinstance(self.duplicate_policy, DuplicatePolicy):
            raise AnalysisConfigurationError("duplicate_policy must be one_per_setup")


def load_signal_engine_config(path: str | Path) -> SignalEngineConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("signal_engine")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load signal engine configuration: {exc}") from exc
    names = {field.name for field in fields(SignalEngineConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[signal_engine] must contain exactly: " + ", ".join(sorted(names))
        )
    values = dict(table)
    try:
        values["duplicate_policy"] = DuplicatePolicy(values["duplicate_policy"])
    except (ValueError, TypeError) as exc:
        raise AnalysisConfigurationError("unsupported duplicate_policy") from exc
    return SignalEngineConfig(**values)
