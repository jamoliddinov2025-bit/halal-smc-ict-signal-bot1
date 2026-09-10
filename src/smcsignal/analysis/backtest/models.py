"""Immutable historical replay and backtest result facts. Not advice or execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from smcsignal.analysis.backtest.config import BacktestConfig
from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.displacement.config import DisplacementConfig
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.fvg.config import FVGConfig
from smcsignal.analysis.halal_filter.config import HalalFilterConfig
from smcsignal.analysis.liquidity.config import LiquidityConfig
from smcsignal.analysis.liquidity.time import candle_close_time
from smcsignal.analysis.mtf.config import MTFConfig
from smcsignal.analysis.mtf.timeframes import require_higher_multiple, timeframe_seconds
from smcsignal.analysis.order_blocks.config import OrderBlockConfig
from smcsignal.analysis.ote.config import OTEConfig
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.outcome_tracking.models import OutcomeSnapshot, OutcomeStatus, SignalOutcome
from smcsignal.analysis.performance.config import PerformanceConfig
from smcsignal.analysis.performance.models import PerformanceReport
from smcsignal.analysis.premium_discount.config import PDConfig
from smcsignal.analysis.provenance import _instant
from smcsignal.analysis.setup_attribution.config import SetupAttributionConfig
from smcsignal.analysis.setup_attribution.models import AttributionSnapshot, SetupLabel
from smcsignal.analysis.setup_quality.config import SetupQualityConfig
from smcsignal.analysis.signal_eligibility.config import SignalEligibilityConfig
from smcsignal.analysis.signal_engine.config import SignalEngineConfig
from smcsignal.analysis.signal_engine.models import SignalDirection, SignalSnapshot
from smcsignal.data.config import SUPPORTED_TIMEFRAMES
from smcsignal.data.models import OHLCV


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _candles(
    value: object, name: str, *, allow_empty: bool, timeframe: str | None
) -> tuple[OHLCV, ...]:
    if not isinstance(value, tuple):
        raise AnalysisInputError(f"{name} must be a tuple of OHLCV candles")
    if not allow_empty and not value:
        raise AnalysisInputError(f"{name} must contain at least one candle")
    previous: OHLCV | None = None
    for candle in value:
        if not isinstance(candle, OHLCV):
            raise AnalysisInputError(f"{name} must contain OHLCV candles")
        if previous is not None:
            if candle.timestamp <= previous.timestamp:
                raise AnalysisInputError(
                    f"{name} timestamps must be strictly chronological and unique"
                )
            if timeframe is not None and candle.timestamp < candle_close_time(
                previous.timestamp, timeframe
            ):
                raise AnalysisInputError(f"{name} candles must not overlap within the timeframe")
        previous = candle
    return value


@dataclass(frozen=True, slots=True)
class ReplayDataset:
    """One declared historical dataset: primary candles plus HTF histories.

    The dataset is the exact history a replay draws from; nothing outside it
    is read. Candles are chronological, unique, and nonoverlapping. Higher
    timeframe histories may be empty (the MTF layer then reports no completed
    HTF context). This is offline historical data only; no exchange, network,
    or live feed is involved.
    """

    symbol: str
    timeframe: str
    candles: tuple[OHLCV, ...]
    higher_candles: Mapping[str, tuple[OHLCV, ...]] = field(default_factory=dict)
    venue: str = "synthetic_spot"
    provider: str = "csv"
    dataset_id: str = "backtest-dataset:v1"

    def __post_init__(self) -> None:
        for name, value in (
            ("symbol", self.symbol),
            ("timeframe", self.timeframe),
            ("venue", self.venue),
            ("provider", self.provider),
            ("dataset_id", self.dataset_id),
        ):
            _text(value, name)
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            raise AnalysisInputError("timeframe must be one of: " + ", ".join(SUPPORTED_TIMEFRAMES))
        if not isinstance(self.higher_candles, Mapping):
            raise AnalysisInputError("higher_candles must map timeframe strings to candle tuples")
        frozen: dict[str, tuple[OHLCV, ...]] = {}
        for key, history in self.higher_candles.items():
            _text(key, "higher timeframe key")
            if key == self.timeframe:
                raise AnalysisInputError("a higher timeframe cannot equal the primary timeframe")
            timeframe_seconds(key)
            require_higher_multiple(self.timeframe, key)
            frozen[key] = _candles(
                history, f"higher_candles[{key}]", allow_empty=True, timeframe=key
            )
        object.__setattr__(
            self,
            "candles",
            _candles(self.candles, "candles", allow_empty=False, timeframe=self.timeframe),
        )
        object.__setattr__(self, "higher_candles", MappingProxyType(frozen))


def _default_liquidity() -> LiquidityConfig:
    """The documented example unit; every shipped configuration uses USDT."""

    return LiquidityConfig("USDT")


@dataclass(frozen=True, slots=True)
class BacktestConfiguration:
    """One frozen replay configuration over the existing pipeline.

    Every field is an existing Phase 3–19 configuration consumed unchanged.
    The bundle only cross-validates invariants the pipeline itself enforces
    at runtime (the setup-quality and signal-engine thresholds must match)
    so an inconsistent replay fails before any candle is processed. No
    second strategy, detector, or scoring rule exists here.
    """

    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    analysis: AnalysisConfig | None = None
    liquidity: LiquidityConfig = field(default_factory=_default_liquidity)
    displacement: DisplacementConfig = field(default_factory=DisplacementConfig)
    fvg: FVGConfig = field(default_factory=FVGConfig)
    order_blocks: OrderBlockConfig = field(default_factory=OrderBlockConfig)
    premium_discount: PDConfig = field(default_factory=PDConfig)
    ote: OTEConfig = field(default_factory=OTEConfig)
    mtf: MTFConfig = field(default_factory=MTFConfig)
    halal_filter: HalalFilterConfig = field(default_factory=HalalFilterConfig)
    setup_quality: SetupQualityConfig = field(default_factory=SetupQualityConfig)
    signal_eligibility: SignalEligibilityConfig = field(default_factory=SignalEligibilityConfig)
    signal_engine: SignalEngineConfig = field(default_factory=SignalEngineConfig)
    outcome_tracking: OutcomeTrackingConfig = field(default_factory=OutcomeTrackingConfig)
    setup_attribution: SetupAttributionConfig = field(default_factory=SetupAttributionConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.backtest, BacktestConfig):
            raise AnalysisConfigurationError("backtest requires BacktestConfig")
        if self.analysis is not None and not isinstance(self.analysis, AnalysisConfig):
            raise AnalysisConfigurationError("analysis must be AnalysisConfig or None")
        for name, kind in (
            ("liquidity", LiquidityConfig),
            ("displacement", DisplacementConfig),
            ("fvg", FVGConfig),
            ("order_blocks", OrderBlockConfig),
            ("premium_discount", PDConfig),
            ("ote", OTEConfig),
            ("mtf", MTFConfig),
            ("halal_filter", HalalFilterConfig),
            ("setup_quality", SetupQualityConfig),
            ("signal_eligibility", SignalEligibilityConfig),
            ("signal_engine", SignalEngineConfig),
            ("outcome_tracking", OutcomeTrackingConfig),
            ("setup_attribution", SetupAttributionConfig),
            ("performance", PerformanceConfig),
        ):
            if not isinstance(getattr(self, name), kind):
                raise AnalysisConfigurationError(f"{name} must be {kind.__name__}")
        if self.signal_engine.publish_threshold != self.setup_quality.publish_threshold:
            raise AnalysisConfigurationError(
                "signal_engine.publish_threshold must equal setup_quality.publish_threshold"
            )


@dataclass(frozen=True, slots=True)
class ReplayStep:
    """The leaf frames of one replayed primary candle, in draw order."""

    index: int
    signal: SignalSnapshot
    outcome: OutcomeSnapshot
    attribution: AttributionSnapshot

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise AnalysisInputError("index must be a nonnegative integer")
        if not isinstance(self.signal, SignalSnapshot):
            raise AnalysisInputError("replay steps require existing SignalSnapshot frames")
        if not isinstance(self.outcome, OutcomeSnapshot):
            raise AnalysisInputError("replay steps require existing OutcomeSnapshot frames")
        if not isinstance(self.attribution, AttributionSnapshot):
            raise AnalysisInputError("replay steps require existing AttributionSnapshot frames")
        if self.outcome.upstream is not self.signal or self.attribution.upstream is not self.signal:
            raise AnalysisInputError("replay step frames must replay the same signal frame")


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """One complete chronological replay of one dataset.

    Frames are the unchanged Phase 17–19 publications; the replay neither
    regenerates nor mutates them. ``replay_id`` is a deterministic digest of
    the configuration, the dataset identity, the exact candle prefixes fed
    through the chain, and every published record, so identical runs of
    identical histories carry identical identities.
    """

    dataset: ReplayDataset
    configuration: BacktestConfiguration
    steps: tuple[ReplayStep, ...]
    replay_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, ReplayDataset):
            raise AnalysisInputError("replay results require a ReplayDataset")
        if not isinstance(self.configuration, BacktestConfiguration):
            raise AnalysisInputError("replay results require a BacktestConfiguration")
        if not isinstance(self.steps, tuple) or not self.steps:
            raise AnalysisInputError("replay results require at least one replay step")
        for position, step in enumerate(self.steps):
            if not isinstance(step, ReplayStep):
                raise AnalysisInputError("replay results contain replay steps only")
            if step.index != position:
                raise AnalysisInputError("replay steps must start at zero in consecutive order")
        _text(self.replay_id, "replay_id")
        if not self.replay_id.startswith("backtest-replay:"):
            raise AnalysisInputError("replay ids use the backtest-replay prefix")

    @property
    def signal_frames(self) -> tuple[SignalSnapshot, ...]:
        """Every published signal frame, in replay order."""

        return tuple(step.signal for step in self.steps)

    @property
    def outcomes(self) -> tuple[OutcomeSnapshot, ...]:
        """Every published outcome frame, in replay order."""

        return tuple(step.outcome for step in self.steps)

    @property
    def attributions(self) -> tuple[AttributionSnapshot, ...]:
        """Every published attribution frame, in replay order."""

        return tuple(step.attribution for step in self.steps)


def _finalized_field(name: str, value: object, finalized: bool) -> None:
    if finalized and value is None:
        raise AnalysisInputError(f"{name} exists on finalized outcomes")
    if not finalized and value is not None:
        raise AnalysisInputError(f"{name} must be None while an outcome is open")


@dataclass(frozen=True, slots=True)
class BacktestSignalResult:
    """One BUY_SIGNAL with its attribution profile and latest outcome version.

    Every value is copied from the existing Phase 17–19 records or computed
    with the existing Phase 18 helpers; no classification or statistic is
    recomputed here. This is a research record of what was published and what
    the fixed-horizon outcome was, never an order, fill, or advice.
    """

    dataset_key: str
    signal_id: str
    setup_identity: str
    symbol: str
    timeframe: str
    candle_index: int
    opened_at: datetime
    closed_at: datetime
    direction: SignalDirection
    score_total: int
    labels: tuple[SetupLabel, ...]
    combination_key: str
    outcome_id: str
    outcome_status: OutcomeStatus
    reference_close: Decimal
    final_index: int | None
    final_close: Decimal | None
    final_return: Decimal | None
    mfe_return: Decimal | None
    mae_return: Decimal | None
    outcome: SignalOutcome

    def __post_init__(self) -> None:
        for name, value in (
            ("dataset_key", self.dataset_key),
            ("signal_id", self.signal_id),
            ("setup_identity", self.setup_identity),
            ("symbol", self.symbol),
            ("timeframe", self.timeframe),
            ("outcome_id", self.outcome_id),
        ):
            _text(value, name)
        if type(self.candle_index) is not int or self.candle_index < 0:
            raise AnalysisInputError("candle_index must be a nonnegative integer")
        object.__setattr__(self, "opened_at", _instant(self.opened_at, "opened_at"))
        object.__setattr__(self, "closed_at", _instant(self.closed_at, "closed_at"))
        if self.closed_at <= self.opened_at:
            raise AnalysisInputError("closed_at must follow opened_at")
        if (
            not isinstance(self.direction, SignalDirection)
            or self.direction is not SignalDirection.LONG
        ):
            raise AnalysisInputError("backtests cover published LONG spot signals only")
        if type(self.score_total) is not int or not 0 <= self.score_total <= 100:
            raise AnalysisInputError("score_total must be an integer from 0 to 100")
        if not isinstance(self.labels, tuple) or not all(
            isinstance(label, SetupLabel) for label in self.labels
        ):
            raise AnalysisInputError("labels must be a tuple of SetupLabel values")
        _text(self.combination_key, "combination_key")
        if not isinstance(self.outcome_status, OutcomeStatus):
            raise AnalysisInputError("outcome_status must be OPEN, WIN, LOSS, or FLAT")
        if not isinstance(self.reference_close, Decimal) or not self.reference_close.is_finite():
            raise AnalysisInputError("reference_close must be a finite Decimal")
        if self.reference_close <= 0:
            raise AnalysisInputError("reference_close must be a positive Decimal price")
        finalized = self.outcome_status is not OutcomeStatus.OPEN
        for final_name, final_value in (
            ("final_index", self.final_index),
            ("final_close", self.final_close),
            ("final_return", self.final_return),
        ):
            _finalized_field(final_name, final_value, finalized)
        if self.final_index is not None and (
            type(self.final_index) is not int or self.final_index < 0
        ):
            raise AnalysisInputError("final_index must be a nonnegative integer")
        for ratio_name, ratio in (
            ("final_close", self.final_close),
            ("final_return", self.final_return),
            ("mfe_return", self.mfe_return),
            ("mae_return", self.mae_return),
        ):
            if ratio is not None and (not isinstance(ratio, Decimal) or not ratio.is_finite()):
                raise AnalysisInputError(f"{ratio_name} must be a finite Decimal or None")
        if finalized and (self.mfe_return is None or self.mae_return is None):
            raise AnalysisInputError("finalized outcomes carry MFE and MAE extremes")
        if not isinstance(self.outcome, SignalOutcome):
            raise AnalysisInputError("backtest rows retain the latest SignalOutcome version")

    @property
    def finalized(self) -> bool:
        """True when the fixed-horizon outcome is complete."""

        return self.outcome_status is not OutcomeStatus.OPEN


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """One deterministic multi-dataset backtest over unchanged pipeline facts.

    Aggregates are the existing Phase 19c performance report composed over
    every replay; nothing is recomputed or reclassified here. Rows are one
    per published BUY_SIGNAL, sorted chronologically with signal identity as
    the tiebreaker. Historical research output only: no orders, execution,
    optimization, or advice.
    """

    settings: BacktestConfig
    configuration: BacktestConfiguration
    replays: tuple[ReplayResult, ...]
    performance: PerformanceReport
    signals: tuple[BacktestSignalResult, ...]
    backtest_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.settings, BacktestConfig):
            raise AnalysisInputError("backtest reports require BacktestConfig")
        if not isinstance(self.configuration, BacktestConfiguration):
            raise AnalysisInputError("backtest reports require a BacktestConfiguration")
        if not isinstance(self.replays, tuple) or not self.replays:
            raise AnalysisInputError("a backtest covers at least one replay")
        keys: list[str] = []
        for replay in self.replays:
            if not isinstance(replay, ReplayResult):
                raise AnalysisInputError("backtest reports contain replay results only")
            keys.append(
                f"{replay.dataset.symbol}:{replay.dataset.timeframe}:"
                f"{replay.dataset.venue}:{replay.dataset.provider}:{replay.dataset.dataset_id}"
            )
        if len(set(keys)) != len(keys):
            raise AnalysisInputError("each dataset is replayed exactly once")
        if not isinstance(self.performance, PerformanceReport):
            raise AnalysisInputError("backtest reports require the existing PerformanceReport")
        if sorted(keys) != list(self.performance.series_keys):
            raise AnalysisInputError("performance series keys must match the replayed datasets")
        if not isinstance(self.signals, tuple) or not all(
            isinstance(row, BacktestSignalResult) for row in self.signals
        ):
            raise AnalysisInputError("backtest report rows must be BacktestSignalResult records")
        ordering = [(row.opened_at, row.signal_id) for row in self.signals]
        if ordering != sorted(ordering):
            raise AnalysisInputError("rows must be sorted by opened_at then signal_id")
        if len({row.signal_id for row in self.signals}) != len(self.signals):
            raise AnalysisInputError("each published signal appears exactly once")
        known = set(keys)
        for row in self.signals:
            if row.dataset_key not in known:
                raise AnalysisInputError("row dataset keys must reference a replayed dataset")
        overall = self.performance.overall
        if len(self.signals) != overall.total_buy_signals:
            raise AnalysisInputError("rows must partition the overall signal population")
        finalized = sum(row.finalized for row in self.signals)
        if (finalized, len(self.signals) - finalized) != (
            overall.finalized_count,
            overall.open_count,
        ):
            raise AnalysisInputError("row statuses must match the overall outcome counts")
        for row in self.signals:
            expected = self.performance.outcome_settings.horizon_bars
            if row.outcome.horizon_bars != expected:
                raise AnalysisInputError("rows must carry the configured outcome horizon")
        _text(self.backtest_id, "backtest_id")
        if not self.backtest_id.startswith("backtest:"):
            raise AnalysisInputError("backtest ids use the backtest prefix")
