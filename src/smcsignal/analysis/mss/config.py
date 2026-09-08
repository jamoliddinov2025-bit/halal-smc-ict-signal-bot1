"""Strict MSS contract flags, not tunable scores or signal thresholds."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "mss-v1"


@dataclass(frozen=True, slots=True)
class MSSConfig:
    enable_displacement_requirement: bool = True
    enable_structure_requirement: bool = True

    def __post_init__(self) -> None:
        for name in ("enable_displacement_requirement", "enable_structure_requirement"):
            value = getattr(self, name)
            if type(value) is not bool or not value:
                raise AnalysisConfigurationError(
                    f"{name} must be true: strict MSS cannot disable displacement or structure"
                )


def load_mss_config(path: str | Path) -> MSSConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("mss")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load MSS configuration: {exc}") from exc
    keys = {"enable_displacement_requirement", "enable_structure_requirement"}
    if not isinstance(table, dict) or set(table) != keys:
        raise AnalysisConfigurationError("[mss] must contain exactly: " + ", ".join(sorted(keys)))
    return MSSConfig(**table)
