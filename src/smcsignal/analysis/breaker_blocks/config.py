"""Strict breaker formation requirements, not signal or lifecycle settings."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "breaker-v1"


class InvalidationBasis(StrEnum):
    CLOSE_THROUGH_FAR_BOUNDARY = "close_through_far_boundary"


class BreakerZoneBasis(StrEnum):
    ORIGINAL_ORDER_BLOCK = "original_order_block"


@dataclass(frozen=True, slots=True)
class BreakerBlockConfig:
    require_displacement: bool = True
    require_mss: bool = True
    invalidation_basis: InvalidationBasis = InvalidationBasis.CLOSE_THROUGH_FAR_BOUNDARY
    zone_basis: BreakerZoneBasis = BreakerZoneBasis.ORIGINAL_ORDER_BLOCK

    def __post_init__(self) -> None:
        for name in ("require_displacement", "require_mss"):
            if type(getattr(self, name)) is not bool or not getattr(self, name):
                raise AnalysisConfigurationError(f"{name} must be true in strict breaker-v1")
        if not isinstance(self.invalidation_basis, InvalidationBasis):
            raise AnalysisConfigurationError(
                "invalidation_basis must be close_through_far_boundary"
            )
        if not isinstance(self.zone_basis, BreakerZoneBasis):
            raise AnalysisConfigurationError("zone_basis must be original_order_block")


def load_breaker_block_config(path: str | Path) -> BreakerBlockConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("breaker_blocks")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load Breaker configuration: {exc}") from exc
    names = {f.name for f in fields(BreakerBlockConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[breaker_blocks] must contain exactly: " + ", ".join(sorted(names))
        )
    values = dict(table)
    for name, enum in (("invalidation_basis", InvalidationBasis), ("zone_basis", BreakerZoneBasis)):
        try:
            values[name] = enum(values[name])
        except (ValueError, TypeError) as exc:
            raise AnalysisConfigurationError(f"unsupported {name}") from exc
    return BreakerBlockConfig(**values)
