"""Strict robustness-v1 invariants; walk-forward validation, never optimization."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

METHODOLOGY_VERSION = "robustness-v1"

DEFAULT_DEVELOPMENT_BARS = 50
DEFAULT_VALIDATION_BARS = 25
DEFAULT_STEP_BARS = 25
DEFAULT_REGIME_LOOKBACK_BARS = 20
DEFAULT_REGIME_BASELINE_MULTIPLE = 4
DEFAULT_TREND_THRESHOLD = Decimal("0.30")
DEFAULT_HIGH_VOLATILITY_THRESHOLD = Decimal("1.50")
DEFAULT_LOW_VOLATILITY_THRESHOLD = Decimal("0.75")
DEFAULT_MINIMUM_FINALIZED_FOR_STABILITY = 10
DEFAULT_MINIMUM_WINDOWS_FOR_STABILITY = 3
DEFAULT_STABILITY_WIN_RATE_FLOOR = Decimal("0.50")
DEFAULT_MAXIMUM_WIN_RATE_SPREAD = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class RobustnessConfig:
    """Declare frozen walk-forward and robustness invariants.

    Walk-forward windows are sequential and chronological: each window pairs a
    development period with the validation period that immediately follows it.
    ``step_bars`` must be at least ``validation_bars``, so out-of-sample
    validation segments never overlap; development segments may overlap when
    the step is smaller than a full window. Nothing is fitted, tuned, or
    selected: development periods are descriptive references only, and the
    degradation deltas compare validation statistics against them.

    Regime settings define one deterministic, causal, exact-Decimal
    classification for research annotations only. The efficiency ratio is the
    absolute net close move over the lookback divided by the summed absolute
    close-to-close moves; the volatility ratio compares the recent lookback
    mean absolute close change against the longer baseline-window mean.
    Precedence is fixed: TRENDING, then HIGH_VOLATILITY, then
    LOW_VOLATILITY, else RANGING. Regimes never generate, veto, or modify
    signals and never alter Phase 20 outcomes.

    Stability thresholds gate descriptive status labels only. No statistical
    significance is claimed anywhere in this layer.
    """

    enabled: bool = True
    development_bars: int = DEFAULT_DEVELOPMENT_BARS
    validation_bars: int = DEFAULT_VALIDATION_BARS
    step_bars: int = DEFAULT_STEP_BARS
    regime_lookback_bars: int = DEFAULT_REGIME_LOOKBACK_BARS
    regime_baseline_multiple: int = DEFAULT_REGIME_BASELINE_MULTIPLE
    trend_threshold: Decimal = DEFAULT_TREND_THRESHOLD
    high_volatility_threshold: Decimal = DEFAULT_HIGH_VOLATILITY_THRESHOLD
    low_volatility_threshold: Decimal = DEFAULT_LOW_VOLATILITY_THRESHOLD
    minimum_finalized_for_stability: int = DEFAULT_MINIMUM_FINALIZED_FOR_STABILITY
    minimum_windows_for_stability: int = DEFAULT_MINIMUM_WINDOWS_FOR_STABILITY
    stability_win_rate_floor: Decimal = DEFAULT_STABILITY_WIN_RATE_FLOOR
    maximum_win_rate_spread: Decimal = DEFAULT_MAXIMUM_WIN_RATE_SPREAD

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not self.enabled:
            raise AnalysisConfigurationError("enabled must be true in strict robustness-v1")
        for name, value in (
            ("development_bars", self.development_bars),
            ("validation_bars", self.validation_bars),
            ("step_bars", self.step_bars),
            ("regime_lookback_bars", self.regime_lookback_bars),
            ("regime_baseline_multiple", self.regime_baseline_multiple),
            ("minimum_finalized_for_stability", self.minimum_finalized_for_stability),
            ("minimum_windows_for_stability", self.minimum_windows_for_stability),
        ):
            if type(value) is not int or value < 1:
                raise AnalysisConfigurationError(f"{name} must be an integer from 1 upward")
        if self.step_bars < self.validation_bars:
            raise AnalysisConfigurationError(
                "step_bars must be at least validation_bars so validation segments never overlap"
            )
        for threshold_name, threshold in (
            ("trend_threshold", self.trend_threshold),
            ("high_volatility_threshold", self.high_volatility_threshold),
            ("low_volatility_threshold", self.low_volatility_threshold),
            ("stability_win_rate_floor", self.stability_win_rate_floor),
            ("maximum_win_rate_spread", self.maximum_win_rate_spread),
        ):
            if not isinstance(threshold, Decimal) or not threshold.is_finite() or threshold <= 0:
                raise AnalysisConfigurationError(
                    f"{threshold_name} must be a positive, finite Decimal"
                )
        if self.trend_threshold > 1:
            raise AnalysisConfigurationError("trend_threshold cannot exceed one")
        if self.stability_win_rate_floor > 1:
            raise AnalysisConfigurationError("stability_win_rate_floor cannot exceed one")
        if self.maximum_win_rate_spread > 1:
            raise AnalysisConfigurationError("maximum_win_rate_spread cannot exceed one")
        if self.low_volatility_threshold >= self.high_volatility_threshold:
            raise AnalysisConfigurationError(
                "low_volatility_threshold must be below high_volatility_threshold"
            )

    @property
    def regime_baseline_bars(self) -> int:
        """Baseline window length in close-to-close changes."""

        return self.regime_lookback_bars * self.regime_baseline_multiple


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise AnalysisConfigurationError(f"{name} must be a decimal string") from exc
    raise AnalysisConfigurationError(f"{name} must be a decimal string")


def load_robustness_config(path: str | Path) -> RobustnessConfig:
    """Load the exact approved [robustness] table; unknown keys are rejected."""

    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("robustness")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load robustness configuration: {exc}") from exc
    names = {field.name for field in fields(RobustnessConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[robustness] must contain exactly: " + ", ".join(sorted(names))
        )
    for key in table:
        if not isinstance(table[key], (bool, int, str)):
            raise AnalysisConfigurationError(f"{key} must be a boolean, integer, or decimal string")
    return RobustnessConfig(
        enabled=table["enabled"],
        development_bars=table["development_bars"],
        validation_bars=table["validation_bars"],
        step_bars=table["step_bars"],
        regime_lookback_bars=table["regime_lookback_bars"],
        regime_baseline_multiple=table["regime_baseline_multiple"],
        trend_threshold=_decimal(table["trend_threshold"], "trend_threshold"),
        high_volatility_threshold=_decimal(
            table["high_volatility_threshold"], "high_volatility_threshold"
        ),
        low_volatility_threshold=_decimal(
            table["low_volatility_threshold"], "low_volatility_threshold"
        ),
        minimum_finalized_for_stability=table["minimum_finalized_for_stability"],
        minimum_windows_for_stability=table["minimum_windows_for_stability"],
        stability_win_rate_floor=_decimal(
            table["stability_win_rate_floor"], "stability_win_rate_floor"
        ),
        maximum_win_rate_spread=_decimal(
            table["maximum_win_rate_spread"], "maximum_win_rate_spread"
        ),
    )
