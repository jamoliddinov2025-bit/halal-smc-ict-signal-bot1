"""Phase 23A import isolation and no-baseline-mutation guarantees."""

from __future__ import annotations

import smcsignal
from smcsignal.analysis import improvement


def test_improvement_import_does_not_drag_in_production_side_effects() -> None:
    # Importing the improvement namespace must not require or alter any
    # trading/network production component; it is a pure models+state module.
    for component in ("trading", "exchange", "broker", "telegram", "live"):
        assert component not in improvement.__dict__


def test_version_is_unchanged_baseline() -> None:
    assert smcsignal.__version__ == "0.22.0"


def test_top_level_facade_is_not_grown() -> None:
    # Phase 23 lives under smcsignal.analysis.improvement only; it adds no
    # new top-level attribute to the package.
    assert not hasattr(smcsignal, "improvement")


def test_public_api_does_not_expose_evaluator_or_production_tools() -> None:
    public = set(improvement.__all__)
    banned = {"evaluator", "backtest", "robustness", "intelligence", "optimize", "promote"}
    assert not (public & banned)
