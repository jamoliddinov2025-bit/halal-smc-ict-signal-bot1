"""Closed candidate-change allow-list over frozen Phase 1-22 config settings.

A candidate may propose a change only to a setting that appears here. Each
``ChangeSurface`` names a real, already-frozen configuration attribute, records
its frozen baseline value, and declares the kind and Python value type of legal
new values. Everything outside this closed list is rejected, so no undeclared
or free-form code change, and no change to enablement, weights, eligibility,
halal status, or any non-enumerated surface, can ever be proposed as a delta.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError


# A delta against an enumerable, already-frozen configuration setting.
class ChangeKind(StrEnum):
    PARAMETER = "parameter"
    THRESHOLD = "threshold"


@dataclass(frozen=True, slots=True)
class ChangeSurface:
    """One changeable frozen setting plus the value contract for its new value.

    ``baseline_value`` is the frozen value of the setting at the Phase 22
    baseline; a candidate delta's ``old_value`` must equal it so every delta is
    anchored to the real baseline and an invalid old-value declaration is
    rejected.
    """

    component: str
    setting: str
    kind: ChangeKind
    value_type: str  # "int" or "decimal"
    baseline_value: int | Decimal
    note: str = ""

    def __post_init__(self) -> None:
        if not self.component.strip() or self.component != self.component.strip():
            raise AnalysisConfigurationError("surface component must be a nonempty, trimmed string")
        if not self.setting.strip() or self.setting != self.setting.strip():
            raise AnalysisConfigurationError("surface setting must be a nonempty, trimmed string")
        if not isinstance(self.kind, ChangeKind):
            raise AnalysisConfigurationError("surface kind must be a ChangeKind")
        if self.value_type not in ("int", "decimal"):
            raise AnalysisConfigurationError("surface value_type must be int or decimal")
        if isinstance(self.baseline_value, Decimal):
            if not self.baseline_value.is_finite():
                raise AnalysisConfigurationError("surface baseline_value must be finite")
        elif type(self.baseline_value) is not int:
            raise AnalysisConfigurationError("surface baseline_value must be an int or Decimal")

    @property
    def key(self) -> str:
        """Deterministic ``component.setting`` key for a delta target."""
        return f"{self.component}.{self.setting}"


ALLOWED_SURFACES: tuple[ChangeSurface, ...] = (
    ChangeSurface(
        "setup_quality",
        "publish_threshold",
        ChangeKind.THRESHOLD,
        "int",
        75,
        "Integer pass threshold from 0 to 100.",
    ),
    ChangeSurface(
        "signal_engine",
        "publish_threshold",
        ChangeKind.THRESHOLD,
        "int",
        75,
        "Integer publication threshold from 0 to 100.",
    ),
    ChangeSurface(
        "displacement",
        "atr_period",
        ChangeKind.PARAMETER,
        "int",
        14,
        "Prior-ATR period; a positive integer.",
    ),
    ChangeSurface(
        "displacement",
        "min_body_atr",
        ChangeKind.PARAMETER,
        "decimal",
        Decimal("1.0"),
        "Minimum body in ATR units; a positive finite Decimal.",
    ),
    ChangeSurface(
        "displacement",
        "min_range_atr",
        ChangeKind.PARAMETER,
        "decimal",
        Decimal("1.5"),
        "Minimum range in ATR units; a positive finite Decimal.",
    ),
    ChangeSurface(
        "ote",
        "lower_retracement",
        ChangeKind.PARAMETER,
        "decimal",
        Decimal("0.62"),
        "Lower OTE retracement; must stay below the baseline upper bound.",
    ),
    ChangeSurface(
        "ote",
        "upper_retracement",
        ChangeKind.PARAMETER,
        "decimal",
        Decimal("0.79"),
        "Upper OTE retracement; must stay above the baseline lower bound.",
    ),
)


def _surfaces_by_key() -> dict[str, ChangeSurface]:
    return {surface.key: surface for surface in ALLOWED_SURFACES}


def allowed_keys() -> tuple[str, ...]:
    """The closed, sorted list of changeable surface keys."""
    return tuple(sorted(_surfaces_by_key()))


def lookup_surface(component: str, setting: str) -> ChangeSurface:
    """Resolve a surface, rejecting anything outside the allow-list."""
    surface = _surfaces_by_key().get(f"{component}.{setting}")
    if surface is None:
        raise AnalysisInputError(
            f"change target {component}.{setting} is not on the closed candidate allow-list"
        )
    return surface


def _require_int(value: object, surface: ChangeSurface) -> int:
    if type(value) is not int:
        raise AnalysisInputError(f"{surface.key} requires an integer value")
    return value


def _require_decimal(value: object, surface: ChangeSurface) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AnalysisInputError(f"{surface.key} requires a finite Decimal value")
    return value


def validate_new_value(value: object, surface: ChangeSurface) -> None:
    """Reject a new value that is the wrong type or out of the surface bounds."""
    if surface.kind is ChangeKind.THRESHOLD:
        integer = _require_int(value, surface)
        if not 0 <= integer <= 100:
            raise AnalysisInputError(f"{surface.key} threshold must be an integer from 0 to 100")
        return
    if surface.component == "displacement" and surface.setting == "atr_period":
        integer = _require_int(value, surface)
        if integer < 1:
            raise AnalysisInputError(f"{surface.key} must be a positive integer")
        return
    if surface.component == "displacement" and surface.setting in (
        "min_body_atr",
        "min_range_atr",
    ):
        decimal = _require_decimal(value, surface)
        if decimal <= 0:
            raise AnalysisInputError(f"{surface.key} must be a positive Decimal")
        return
    if surface.component == "ote" and surface.setting == "lower_retracement":
        decimal = _require_decimal(value, surface)
        if not Decimal(0) < decimal < Decimal("0.79"):
            raise AnalysisInputError("ote.lower_retracement must satisfy 0 < value < 0.79")
        return
    if surface.component == "ote" and surface.setting == "upper_retracement":
        decimal = _require_decimal(value, surface)
        if not Decimal("0.62") < decimal < Decimal(1):
            raise AnalysisInputError("ote.upper_retracement must satisfy 0.62 < value < 1")
        return
    raise AnalysisInputError(f"no value contract defined for {surface.key}")
