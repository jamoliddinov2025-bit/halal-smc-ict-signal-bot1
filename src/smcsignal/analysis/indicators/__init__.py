"""Phase 19a API: supporting indicator context over existing displacement frames."""

from smcsignal.analysis.indicators.analyzer import (
    IndicatorAnalyzer,
    analyze_indicators,
)
from smcsignal.analysis.indicators.calculation import (
    EMACalculator,
    RSICalculator,
    VolumeAverageCalculator,
    average,
    ema_seed,
    ema_update,
    rsi_from_averages,
    volume_ratio,
    wilder_update,
)
from smcsignal.analysis.indicators.config import (
    MAX_PERIOD,
    METHODOLOGY_VERSION,
    IndicatorsConfig,
    load_indicators_config,
)
from smcsignal.analysis.indicators.models import (
    IndicatorSnapshot,
    current_observation,
)

__all__ = [
    "EMACalculator",
    "IndicatorAnalyzer",
    "IndicatorSnapshot",
    "IndicatorsConfig",
    "MAX_PERIOD",
    "METHODOLOGY_VERSION",
    "RSICalculator",
    "VolumeAverageCalculator",
    "analyze_indicators",
    "average",
    "current_observation",
    "ema_seed",
    "ema_update",
    "load_indicators_config",
    "rsi_from_averages",
    "volume_ratio",
    "wilder_update",
]
