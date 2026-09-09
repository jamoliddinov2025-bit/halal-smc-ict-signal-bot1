"""Phase 22 pattern and diagnostic classification matrix (unit tests)."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.intelligence import (
    DiagnosticLabel,
    IntelligencePattern,
    diagnose,
)
from smcsignal.analysis.outcome_tracking.models import AnalyticsSummary
from tests.intelligence.helpers import intelligence


def _summary(*, win_rate: Decimal, average: Decimal, finalized: int) -> AnalyticsSummary:
    """A fabricated exact summary carrying the classification inputs only."""
    return AnalyticsSummary(
        total_buy_signals=finalized,
        open_count=0,
        win_count=max(0, round(float(win_rate) * finalized)),
        loss_count=finalized - max(0, round(float(win_rate) * finalized)),
        flat_count=0,
        finalized_count=finalized,
        final_return_sum=average * finalized,
        mfe_return_sum=average * finalized,
        mae_return_sum=Decimal("0"),
        win_rate=win_rate,
        average_final_return=average,
        average_mfe_return=average,
        average_mae_return=Decimal("0"),
    )


def test_below_diagnosis_minimum_is_undersampled() -> None:
    config = intelligence(minimum_finalized_for_diagnosis=10)
    summary = _summary(win_rate=Decimal("1"), average=Decimal("0.1"), finalized=5)
    sufficient, pattern, diagnostic = diagnose(summary, config)
    assert sufficient is False
    assert pattern is IntelligencePattern.UNDERSAMPLED
    assert diagnostic is DiagnosticLabel.UNDETERMINED


def test_high_win_rate_positive_average_is_winner_strength() -> None:
    config = intelligence(minimum_finalized_for_diagnosis=1)
    summary = _summary(win_rate=Decimal("0.70"), average=Decimal("0.05"), finalized=10)
    sufficient, pattern, diagnostic = diagnose(summary, config)
    assert sufficient is True
    assert pattern is IntelligencePattern.WINNER
    assert diagnostic is DiagnosticLabel.STRENGTH


def test_low_win_rate_negative_average_is_loser_weakness() -> None:
    config = intelligence(minimum_finalized_for_diagnosis=1)
    summary = _summary(win_rate=Decimal("0.20"), average=Decimal("-0.05"), finalized=10)
    _s, pattern, diagnostic = diagnose(summary, config)
    assert pattern is IntelligencePattern.LOSER
    assert diagnostic is DiagnosticLabel.WEAKNESS


def test_middle_band_is_neutral_undetermined() -> None:
    config = intelligence(minimum_finalized_for_diagnosis=1)
    summary = _summary(win_rate=Decimal("0.50"), average=Decimal("-0.01"), finalized=10)
    _s, pattern, diagnostic = diagnose(summary, config)
    assert pattern is IntelligencePattern.NEUTRAL
    assert diagnostic is DiagnosticLabel.UNDETERMINED


def test_high_win_rate_but_negative_average_is_not_a_winner() -> None:
    config = intelligence(minimum_finalized_for_diagnosis=1)
    summary = _summary(win_rate=Decimal("0.70"), average=Decimal("-0.05"), finalized=10)
    _s, pattern, diagnostic = diagnose(summary, config)
    assert pattern is IntelligencePattern.NEUTRAL
    assert diagnostic is DiagnosticLabel.UNDETERMINED


def test_diagnosis_requires_an_analytics_summary() -> None:
    import pytest

    from smcsignal.analysis.errors import AnalysisInputError

    with pytest.raises(AnalysisInputError):
        diagnose("not-a-summary", intelligence())  # type: ignore[arg-type]


def test_config_object_guard_rejects_non_config() -> None:
    import pytest

    from smcsignal.analysis.errors import AnalysisInputError

    summary = _summary(win_rate=Decimal("0.7"), average=Decimal("0.05"), finalized=10)
    with pytest.raises(AnalysisInputError):
        diagnose(summary, "config")  # type: ignore[arg-type]
