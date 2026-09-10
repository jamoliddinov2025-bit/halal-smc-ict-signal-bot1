"""Strict eligibility-v1 invariants; not a trade, entry, or ranking policy."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "eligibility-v1"


class ConflictPolicy(StrEnum):
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class SignalEligibilityConfig:
    """Declare the frozen eligibility-v1 invariants. No entries or sides are configured."""

    enabled: bool = True
    conflict_policy: ConflictPolicy = ConflictPolicy.NEUTRAL

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict eligibility-v1")
        if not isinstance(self.conflict_policy, ConflictPolicy):
            raise AnalysisConfigurationError("conflict_policy must be neutral")


def load_signal_eligibility_config(path: str | Path) -> SignalEligibilityConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("signal_eligibility")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(
            f"cannot load signal eligibility configuration: {exc}"
        ) from exc
    names = {field.name for field in fields(SignalEligibilityConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[signal_eligibility] must contain exactly: " + ", ".join(sorted(names))
        )
    values = dict(table)
    try:
        values["conflict_policy"] = ConflictPolicy(values["conflict_policy"])
    except (ValueError, TypeError) as exc:
        raise AnalysisConfigurationError("unsupported conflict_policy") from exc
    return SignalEligibilityConfig(**values)
