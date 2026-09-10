"""Strict halal-filter-v1 registry settings; not a religious ruling or signal gate."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "halal-filter-v1"
ASSET_PATTERN = re.compile(r"[A-Za-z0-9]{2,30}")
DEFAULT_ALLOWED_ASSETS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")


class FilterMode(StrEnum):
    ALLOW_LIST = "allow_list"
    DENY_LIST = "deny_list"


def normalize_asset(value: str, *, error: type[Exception] = AnalysisConfigurationError) -> str:
    if not isinstance(value, str) or not ASSET_PATTERN.fullmatch(value):
        raise error(
            "asset symbol must be an exchange symbol such as BTCUSDT (2–30 alphanumeric characters)"
        )
    return value.upper()


def _assets(values: object, name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not all(isinstance(item, str) for item in values):
        raise AnalysisConfigurationError(f"{name} must be a tuple of asset symbols")
    seen: set[str] = set()
    items: list[str] = []
    for item in values:
        symbol = normalize_asset(item)
        if symbol in seen:
            raise AnalysisConfigurationError(f"{name} cannot contain duplicates")
        seen.add(symbol)
        items.append(symbol)
    return tuple(items)


@dataclass(frozen=True, slots=True)
class HalalFilterConfig:
    mode: FilterMode = FilterMode.ALLOW_LIST
    allowed_assets: tuple[str, ...] = DEFAULT_ALLOWED_ASSETS
    denied_assets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, FilterMode):
            raise AnalysisConfigurationError("mode must be allow_list or deny_list")
        allowed = _assets(self.allowed_assets, "allowed_assets")
        denied = _assets(self.denied_assets, "denied_assets")
        object.__setattr__(self, "allowed_assets", allowed)
        object.__setattr__(self, "denied_assets", denied)
        if self.mode is FilterMode.ALLOW_LIST:
            if denied:
                raise AnalysisConfigurationError("denied_assets must be empty in allow_list mode")
            if not allowed:
                raise AnalysisConfigurationError(
                    "allowed_assets must be nonempty in allow_list mode"
                )
            return
        if allowed:
            raise AnalysisConfigurationError("allowed_assets must be empty in deny_list mode")
        if not denied:
            raise AnalysisConfigurationError("denied_assets must be nonempty in deny_list mode")


def _toml_assets(raw: object, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise AnalysisConfigurationError(f"{name} must be a nonempty array of asset symbols")
    return tuple(raw)


def load_halal_filter_config(path: str | Path) -> HalalFilterConfig:
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("halal_filter")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load halal filter configuration: {exc}") from exc
    if not isinstance(table, dict) or "mode" not in table:
        raise AnalysisConfigurationError("[halal_filter] must contain mode")
    try:
        mode = FilterMode(table["mode"])
    except (ValueError, TypeError) as exc:
        raise AnalysisConfigurationError("unsupported halal filter mode") from exc
    if mode is FilterMode.ALLOW_LIST:
        if set(table) != {"mode", "allowed_assets"}:
            raise AnalysisConfigurationError(
                "[halal_filter] allow_list must contain exactly: mode, allowed_assets"
            )
        return HalalFilterConfig(
            mode=mode,
            allowed_assets=_toml_assets(table["allowed_assets"], "allowed_assets"),
            denied_assets=(),
        )
    if set(table) != {"mode", "denied_assets"}:
        raise AnalysisConfigurationError(
            "[halal_filter] deny_list must contain exactly: mode, denied_assets"
        )
    return HalalFilterConfig(
        mode=mode,
        allowed_assets=(),
        denied_assets=_toml_assets(table["denied_assets"], "denied_assets"),
    )
