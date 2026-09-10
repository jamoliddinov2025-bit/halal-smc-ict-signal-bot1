"""Strict setup-attribution-v1 invariants; labels from existing facts only."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "setup-attribution-v1"


@dataclass(frozen=True, slots=True)
class SetupAttributionConfig:
    """Declare frozen attribution invariants.

    Attribution is a deterministic projection of already-published nested
    Phase 15 facts onto a closed label taxonomy. It has no thresholds, no
    outcomes, no invented strategy names, and no future data. The only setting
    is that the layer is enabled.
    """

    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict setup-attribution-v1")


def load_setup_attribution_config(path: str | Path) -> SetupAttributionConfig:
    """Load the exact approved [setup_attribution] table; unknown keys rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("setup_attribution")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(
            f"cannot load setup attribution configuration: {exc}"
        ) from exc
    names = {field.name for field in fields(SetupAttributionConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[setup_attribution] must contain exactly: " + ", ".join(sorted(names))
        )
    return SetupAttributionConfig(**table)
