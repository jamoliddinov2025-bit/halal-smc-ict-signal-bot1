"""Strict, offline Phase 24 presentation/formatting configuration.

This configuration only controls *presentation* (caption limits, what optional
facts are shown). It deliberately exposes **no** setting that can reach signal
generation, SMC/ICT thresholds, indicators, halal classification, or Phase 23
governance, and it carries **no** secret material (no bot token, no chat ids).

Unknown keys or unsafe values are rejected on load. The loader never talks to a
network and never reads environment secrets.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

# Largest single caption a downstream messaging message can carry. The limit is
# a presentation budget only; see ``split_caption`` in the formatting module.
DEFAULT_MAX_CAPTION_LENGTH = 4000


def _flag(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise AnalysisConfigurationError(f"{name} must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    """Frozen presentation settings for deterministic message rendering.

    All values are transport-independent and safe. There is no token, no
    destination identifier, and no strategy-affecting key here.
    """

    enabled: bool = True
    html_escape: bool = True
    max_caption_length: int = DEFAULT_MAX_CAPTION_LENGTH
    split_long_messages: bool = True
    show_signal_id: bool = True
    show_setup_context: bool = True
    show_halal_status: bool = True
    show_timestamps: bool = True
    show_chart_unavailable_marker: bool = True

    def __post_init__(self) -> None:
        _flag(self.enabled, "enabled")
        # HTML escaping is the Phase 24A recommended default and is required for
        # safe deterministic output. Disabling it is not supported.
        if not _flag(self.html_escape, "html_escape"):
            raise AnalysisConfigurationError("html_escape must be true")
        length = self.max_caption_length
        if type(length) is not int or length < 1:
            raise AnalysisConfigurationError("max_caption_length must be a positive integer")
        for name in (
            "split_long_messages",
            "show_signal_id",
            "show_setup_context",
            "show_halal_status",
            "show_timestamps",
            "show_chart_unavailable_marker",
        ):
            _flag(getattr(self, name), name)


def load_delivery_config(path: str | Path) -> DeliveryConfig:
    """Load the exact ``[delivery]`` table; unknown keys are rejected."""
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("delivery")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load delivery configuration: {exc}") from exc
    names = {field.name for field in fields(DeliveryConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[delivery] must contain exactly: " + ", ".join(sorted(names))
        )
    # Construction applies the strict type/value invariants in __post_init__.
    return DeliveryConfig(**table)
