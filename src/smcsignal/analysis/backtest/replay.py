"""Chronological historical replay through the unchanged Phase 3–19 pipeline."""

from __future__ import annotations

from collections.abc import Iterable

from smcsignal.analysis.backtest.calculation import (
    effective_mtf_config,
    replay_rows,
    series_key_for,
)
from smcsignal.analysis.backtest.config import BacktestConfig
from smcsignal.analysis.backtest.evidence import (
    CandlePrefixHasher,
    backtest_identity,
    replay_identity,
)
from smcsignal.analysis.backtest.models import (
    BacktestConfiguration,
    BacktestReport,
    ReplayDataset,
    ReplayResult,
    ReplayStep,
)
from smcsignal.analysis.displacement.analyzer import DisplacementAnalyzer
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.fvg.analyzer import FVGAnalyzer
from smcsignal.analysis.halal_filter.analyzer import HalalFilterAnalyzer
from smcsignal.analysis.liquidity.analyzer import LiquidityAnalyzer
from smcsignal.analysis.mtf.analyzer import MTFAnalyzer
from smcsignal.analysis.order_blocks.analyzer import OrderBlockAnalyzer
from smcsignal.analysis.ote.analyzer import OTEAnalyzer
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.outcome_tracking.analyzer import OutcomeTrackingAnalyzer
from smcsignal.analysis.performance.analyzer import analyze_performance
from smcsignal.analysis.premium_discount.analyzer import PDAnalyzer
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.setup_attribution.analyzer import SetupAttributionAnalyzer
from smcsignal.analysis.setup_quality.analyzer import SetupQualityAnalyzer
from smcsignal.analysis.signal_eligibility.analyzer import SignalEligibilityAnalyzer
from smcsignal.analysis.signal_engine.analyzer import SignalEngineAnalyzer
from smcsignal.data.models import OHLCV


def _detection_chain(
    dataset: ReplayDataset,
    timeframe: str,
    candles: tuple[OHLCV, ...],
    configuration: BacktestConfiguration,
) -> tuple[OTESnapshot, ...]:
    """Run the existing Phase 3–12 chain over one closed history.

    This reuses the existing analyzers unchanged; it exists only to
    materialize higher-timeframe OTE frames for the MTF join, exactly as the
    batch pipeline does. Every frame depends only on candles at or before
    its own index.
    """

    series = SeriesProvenance(
        dataset.symbol, timeframe, dataset.venue, dataset.provider, dataset.dataset_id
    )
    liquidity = LiquidityAnalyzer(
        series=series, config=configuration.liquidity, analysis_config=configuration.analysis
    )
    displacement = DisplacementAnalyzer(
        configuration.displacement, price_unit=configuration.liquidity.price_unit
    )
    fvg = FVGAnalyzer(configuration.fvg)
    order_blocks = OrderBlockAnalyzer(configuration.order_blocks)
    premium_discount = PDAnalyzer(configuration.premium_discount)
    ote = OTEAnalyzer(configuration.ote)
    frames = []
    for candle in candles:
        a = liquidity.update(candle)
        b = displacement.update(a)
        c = fvg.update(b)
        d = order_blocks.update(c)
        e = premium_discount.update(d)
        frames.append(ote.update(e))
    return tuple(frames)


class HistoricalReplay:
    """One chronological, candle-at-a-time replay of one declared dataset.

    The engine draws the next declared candle on every ``update`` and pushes
    it through the existing streaming analyzers; it never reads a candle
    beyond the draw cursor, and higher-timeframe context is gated by the
    existing MTF availability cursor, so no future candle can influence a
    published fact. Results are identical for any chunking of updates and
    for the batch :func:`replay_history` helper.
    """

    def __init__(self, dataset: ReplayDataset, configuration: BacktestConfiguration) -> None:
        if not isinstance(dataset, ReplayDataset):
            raise AnalysisInputError("replay requires a ReplayDataset")
        if not isinstance(configuration, BacktestConfiguration):
            raise AnalysisInputError("replay requires a BacktestConfiguration")
        mtf_config = effective_mtf_config(configuration, dataset)
        if set(dataset.higher_candles) != set(mtf_config.higher_timeframes):
            raise AnalysisInputError(
                "higher_candles must supply exactly the configured higher timeframes: "
                + ", ".join(mtf_config.higher_timeframes)
            )
        higher = {
            timeframe: _detection_chain(
                dataset, timeframe, dataset.higher_candles[timeframe], configuration
            )
            for timeframe in mtf_config.higher_timeframes
        }
        self._dataset = dataset
        self._configuration = configuration
        self._series = SeriesProvenance(
            dataset.symbol, dataset.timeframe, dataset.venue, dataset.provider, dataset.dataset_id
        )
        self._liquidity = LiquidityAnalyzer(
            series=self._series,
            config=configuration.liquidity,
            analysis_config=configuration.analysis,
        )
        self._displacement = DisplacementAnalyzer(
            configuration.displacement, price_unit=configuration.liquidity.price_unit
        )
        self._fvg = FVGAnalyzer(configuration.fvg)
        self._order_blocks = OrderBlockAnalyzer(configuration.order_blocks)
        self._premium_discount = PDAnalyzer(configuration.premium_discount)
        self._ote = OTEAnalyzer(configuration.ote)
        self._mtf = MTFAnalyzer(mtf_config, higher=higher)
        self._halal = HalalFilterAnalyzer(configuration.halal_filter)
        self._quality = SetupQualityAnalyzer(configuration.setup_quality)
        self._eligibility = SignalEligibilityAnalyzer(configuration.signal_eligibility)
        self._engine = SignalEngineAnalyzer(configuration.signal_engine)
        self._outcomes = OutcomeTrackingAnalyzer(configuration.outcome_tracking)
        self._attribution = SetupAttributionAnalyzer(configuration.setup_attribution)
        self._higher = higher
        self._steps: list[ReplayStep] = []
        self._primary_prefix = CandlePrefixHasher(dataset, dataset.timeframe)
        self._higher_prefixes = {
            timeframe: CandlePrefixHasher(dataset, timeframe)
            for timeframe in mtf_config.higher_timeframes
        }
        for timeframe, candles in dataset.higher_candles.items():
            for candle in candles:
                self._higher_prefixes[timeframe].extend(candle)

    @property
    def dataset(self) -> ReplayDataset:
        return self._dataset

    @property
    def configuration(self) -> BacktestConfiguration:
        return self._configuration

    @property
    def series(self) -> SeriesProvenance:
        return self._series

    @property
    def processed_count(self) -> int:
        return len(self._steps)

    @property
    def complete(self) -> bool:
        return len(self._steps) == len(self._dataset.candles)

    @property
    def steps(self) -> tuple[ReplayStep, ...]:
        """Read-only replay steps produced so far, in draw order."""

        return tuple(self._steps)

    def update(self) -> ReplayStep:
        """Draw and replay the next declared candle; strictly chronological."""

        if self.complete:
            raise AnalysisInputError("the declared history is exhausted")
        candle = self._dataset.candles[len(self._steps)]
        self._primary_prefix.extend(candle)
        a = self._liquidity.update(candle)
        b = self._displacement.update(a)
        c = self._fvg.update(b)
        d = self._order_blocks.update(c)
        e = self._premium_discount.update(d)
        f = self._ote.update(e)
        g = self._mtf.update(f)
        h = self._halal.update(g)
        i = self._quality.update(h)
        j = self._eligibility.update(i)
        signal = self._engine.update(j)
        outcome = self._outcomes.update(signal)
        attribution = self._attribution.update(signal)
        step = ReplayStep(len(self._steps), signal, outcome, attribution)
        self._steps.append(step)
        return step

    def result(self) -> ReplayResult:
        """Materialize the finished replay; requires every declared candle."""

        if not self.complete:
            raise AnalysisInputError(
                "a replay result requires the whole declared history to be replayed"
            )
        identity = replay_identity(
            self._configuration,
            self._dataset,
            self._primary_prefix.hexdigest(),
            {timeframe: hasher.hexdigest() for timeframe, hasher in self._higher_prefixes.items()},
            tuple(self._steps),
        )
        return ReplayResult(
            dataset=self._dataset,
            configuration=self._configuration,
            steps=tuple(self._steps),
            replay_id=identity,
        )


def replay_history(dataset: ReplayDataset, configuration: BacktestConfiguration) -> ReplayResult:
    """Batch replay of one declared history; identical to any chunked updates."""

    engine = HistoricalReplay(dataset, configuration)
    while not engine.complete:
        engine.update()
    return engine.result()


def run_backtest(
    datasets: Iterable[ReplayDataset], configuration: BacktestConfiguration
) -> BacktestReport:
    """Replay every dataset and compose the existing Phase 19 performance report.

    One deterministic historical research report over all replays: signal
    rows come from the published Phase 17–19 records, aggregates from the
    unchanged Phase 19c analyzer. No trading, execution, optimization, or
    advice exists here.
    """

    if not isinstance(configuration, BacktestConfiguration):
        raise AnalysisInputError("backtests require a BacktestConfiguration")
    if not isinstance(configuration.backtest, BacktestConfig):
        raise AnalysisConfigurationError("backtests require BacktestConfig")
    try:
        materialized = tuple(datasets)
    except TypeError as exc:
        raise AnalysisInputError("datasets must be an iterable of ReplayDataset records") from exc
    if not materialized:
        raise AnalysisInputError("a backtest replays at least one dataset")
    replays: list[ReplayResult] = []
    keys: list[str] = []
    for dataset in materialized:
        if not isinstance(dataset, ReplayDataset):
            raise AnalysisInputError("datasets must be ReplayDataset records")
        key = series_key_for(dataset)
        if key in keys:
            raise AnalysisInputError("each dataset is replayed exactly once: " + key)
        keys.append(key)
        replays.append(replay_history(dataset, configuration))
    outcomes = {key: replay.outcomes for key, replay in zip(keys, replays, strict=True)}
    attributions = {key: replay.attributions for key, replay in zip(keys, replays, strict=True)}
    performance = analyze_performance(outcomes, attributions, configuration.performance)
    collected = [row for replay in replays for row in replay_rows(replay)]
    rows = tuple(sorted(collected, key=lambda row: (row.opened_at, row.signal_id)))
    identity = backtest_identity(configuration, tuple(replays), performance.report_id)
    return BacktestReport(
        settings=configuration.backtest,
        configuration=configuration,
        replays=tuple(replays),
        performance=performance,
        signals=rows,
        backtest_id=identity,
    )
