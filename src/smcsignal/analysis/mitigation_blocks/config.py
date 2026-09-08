"""Strict first-interaction mitigation settings, not signal or lifecycle policy."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "mitigation-v1"


class InteractionBasis(StrEnum):
    RANGE_INTERSECTION = "range_intersection"


@dataclass(frozen=True, slots=True)
class MitigationBlockConfig:
    interaction_basis: InteractionBasis = InteractionBasis.RANGE_INTERSECTION
    first_interaction_only: bool = True
    ignore_after_breaker: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.interaction_basis, InteractionBasis):
            raise AnalysisConfigurationError("interaction_basis must be range_intersection")
        for name in ("first_interaction_only", "ignore_after_breaker"):
            if type(getattr(self, name)) is not bool or not getattr(self, name):
                raise AnalysisConfigurationError(f"{name} must be true in strict mitigation-v1")


def load_mitigation_block_config(path: str | Path) -> MitigationBlockConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("mitigation_blocks")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load Mitigation configuration: {exc}") from exc
    names = {f.name for f in fields(MitigationBlockConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[mitigation_blocks] must contain exactly: " + ", ".join(sorted(names))
        )
    values = dict(table)
    try:
        values["interaction_basis"] = InteractionBasis(values["interaction_basis"])
    except (ValueError, TypeError) as exc:
        raise AnalysisConfigurationError("unsupported interaction_basis") from exc
    return MitigationBlockConfig(**values)
