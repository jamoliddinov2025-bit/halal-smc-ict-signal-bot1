"""Strict visualization-v1 invariants; rendering facts, never re-detecting them."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "visualization-v1"

MIN_CANVAS = 100
MAX_CANVAS = 10_000
MIN_ROWS = 5
MAX_ROWS = 200


@dataclass(frozen=True, slots=True)
class VisualizationConfig:
    """Declare frozen visualization invariants.

    The drawing model carries semantic style tokens, never colors; renderers
    map tokens deterministically. Canvas numbers are the only settings. The
    layer consumes already-published facts and never re-detects anything, and
    it never generates, gates, or vetoes signals. No raster or PNG output
    exists: renderers emit deterministic SVG and plain text only.
    """

    enabled: bool = True
    svg_width: int = 800
    svg_height: int = 400
    text_rows: int = 24

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict visualization-v1")
        for name, value in (
            ("svg_width", self.svg_width),
            ("svg_height", self.svg_height),
        ):
            if type(value) is not int or not MIN_CANVAS <= value <= MAX_CANVAS:
                raise AnalysisConfigurationError(
                    f"{name} must be an integer from {MIN_CANVAS} to {MAX_CANVAS}"
                )
        if type(self.text_rows) is not int or not MIN_ROWS <= self.text_rows <= MAX_ROWS:
            raise AnalysisConfigurationError(
                f"text_rows must be an integer from {MIN_ROWS} to {MAX_ROWS}"
            )


def load_visualization_config(path: str | Path) -> VisualizationConfig:
    """Load the exact approved [visualization] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("visualization")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load visualization configuration: {exc}") from exc
    names = {field.name for field in fields(VisualizationConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[visualization] must contain exactly: " + ", ".join(sorted(names))
        )
    return VisualizationConfig(**table)
