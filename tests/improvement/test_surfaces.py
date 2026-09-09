"""Phase 23A candidate-change allow-list and value validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement import (
    ALLOWED_SURFACES,
    ChangeKind,
    allowed_keys,
    lookup_surface,
    validate_new_value,
)

THRESHOLD_KEYS = {
    "setup_quality.publish_threshold",
    "signal_engine.publish_threshold",
}


def test_allow_list_contains_only_the_declared_surfaces() -> None:
    assert len(ALLOWED_SURFACES) == 7
    assert allowed_keys() == (
        "displacement.atr_period",
        "displacement.min_body_atr",
        "displacement.min_range_atr",
        "ote.lower_retracement",
        "ote.upper_retracement",
        "setup_quality.publish_threshold",
        "signal_engine.publish_threshold",
    )


def test_surfaces_are_frozen_with_declared_baselines() -> None:
    by_key = {surface.key: surface for surface in ALLOWED_SURFACES}
    assert by_key["setup_quality.publish_threshold"].kind is ChangeKind.THRESHOLD
    assert by_key["setup_quality.publish_threshold"].value_type == "int"
    assert by_key["setup_quality.publish_threshold"].baseline_value == 75
    assert by_key["displacement.atr_period"].kind is ChangeKind.PARAMETER
    assert by_key["displacement.atr_period"].baseline_value == 14
    assert by_key["displacement.min_body_atr"].baseline_value == Decimal("1.0")
    assert by_key["displacement.min_range_atr"].baseline_value == Decimal("1.5")
    assert by_key["ote.lower_retracement"].baseline_value == Decimal("0.62")
    assert by_key["ote.upper_retracement"].baseline_value == Decimal("0.79")


def test_every_allowed_key_resolves_on_the_closed_list() -> None:
    for key in allowed_keys():
        component, _, setting = key.partition(".")
        assert lookup_surface(component, setting).key == key


def test_unknown_surface_is_rejected() -> None:
    with pytest.raises(AnalysisInputError):
        lookup_surface("signal_engine", "weight")
    with pytest.raises(AnalysisInputError):
        lookup_surface("setup_quality", "enabled")
    with pytest.raises(AnalysisInputError):
        lookup_surface("ote", "not_a_surface")


def test_threshold_values_are_bounded() -> None:
    setup = lookup_surface("setup_quality", "publish_threshold")
    signal = lookup_surface("signal_engine", "publish_threshold")
    validate_new_value(0, setup)
    validate_new_value(100, signal)
    for invalid in (-1, 101, "75", 75.0, True):
        with pytest.raises(AnalysisInputError):
            validate_new_value(invalid, setup)


def test_displacement_parameter_values() -> None:
    period = lookup_surface("displacement", "atr_period")
    body = lookup_surface("displacement", "min_body_atr")
    validate_new_value(14, period)
    with pytest.raises(AnalysisInputError):
        validate_new_value(0, period)
    with pytest.raises(AnalysisInputError):
        validate_new_value(-3, period)
    with pytest.raises(AnalysisInputError):
        validate_new_value(Decimal("1.0"), period)  # wrong type for atr_period
    validate_new_value(Decimal("0.75"), body)
    with pytest.raises(AnalysisInputError):
        validate_new_value(Decimal("-1.0"), body)
    with pytest.raises(AnalysisInputError):
        validate_new_value(0.75, body)  # must be Decimal, not float


def test_ote_retracement_values() -> None:
    lower = lookup_surface("ote", "lower_retracement")
    upper = lookup_surface("ote", "upper_retracement")
    validate_new_value(Decimal("0.60"), lower)
    with pytest.raises(AnalysisInputError):
        validate_new_value(Decimal("0.80"), lower)  # must stay below 0.79
    with pytest.raises(AnalysisInputError):
        validate_new_value(Decimal("0.79"), lower)  # equality with upper is rejected
    validate_new_value(Decimal("0.85"), upper)
    with pytest.raises(AnalysisInputError):
        validate_new_value(Decimal("0.50"), upper)  # must stay above 0.62


def test_surfaces_are_immutable() -> None:
    surface = ALLOWED_SURFACES[0]
    with pytest.raises(FrozenInstanceError):
        surface.kind = ChangeKind.PARAMETER  # type: ignore[misc]
