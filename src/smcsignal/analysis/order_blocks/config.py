"""Explicit deterministic formation rules; no scores, entries, or lifecycle flags."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "order-block-v1"


class CandidateSelection(StrEnum):
    NEAREST = "nearest"
    EARLIEST = "earliest"


class ZoneBasis(StrEnum):
    FULL_RANGE = "full_range"
    BODY = "body"


class StructureRequirement(StrEnum):
    BOS_OR_CHOCH = "bos_or_choch"
    BOS = "bos"
    CHOCH = "choch"
    DISPLACEMENT_ONLY = "displacement_only"


@dataclass(frozen=True, slots=True)
class OrderBlockConfig:
    max_candidate_lookback: int = 10
    candidate_selection: CandidateSelection = CandidateSelection.NEAREST
    zone_basis: ZoneBasis = ZoneBasis.FULL_RANGE
    allow_doji: bool = False
    structure_requirement: StructureRequirement = StructureRequirement.BOS_OR_CHOCH
    require_fvg: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.max_candidate_lookback) is not int
            or not 1 <= self.max_candidate_lookback <= 1000
        ):
            raise AnalysisConfigurationError(
                "max_candidate_lookback must be an integer from 1 to 1000"
            )
        for name, expected in (
            ("candidate_selection", CandidateSelection),
            ("zone_basis", ZoneBasis),
            ("structure_requirement", StructureRequirement),
        ):
            if not isinstance(getattr(self, name), expected):
                raise AnalysisConfigurationError(f"{name} must be a {expected.__name__}")
        if type(self.allow_doji) is not bool or type(self.require_fvg) is not bool:
            raise AnalysisConfigurationError("allow_doji and require_fvg must be booleans")


def load_order_block_config(path: str | Path) -> OrderBlockConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("order_blocks")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load Order Block configuration: {exc}") from exc
    expected = {f.name for f in fields(OrderBlockConfig)}
    if not isinstance(table, dict) or set(table) != expected:
        raise AnalysisConfigurationError(
            "[order_blocks] must contain exactly: " + ", ".join(sorted(expected))
        )
    values = dict(table)
    for name, enum in (
        ("candidate_selection", CandidateSelection),
        ("zone_basis", ZoneBasis),
        ("structure_requirement", StructureRequirement),
    ):
        try:
            values[name] = enum(values[name])
        except (TypeError, ValueError) as exc:
            raise AnalysisConfigurationError(f"unsupported {name}") from exc
    return OrderBlockConfig(**values)
