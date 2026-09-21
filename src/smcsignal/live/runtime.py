"""Phase 33 live runtime: the frozen Phase 3-17 chain over live closed candles.

The runtime composes the *existing* public analyzers — liquidity,
displacement, FVG, order blocks, premium/discount, OTE, MTF, halal filter,
setup quality, signal eligibility, and the signal engine — in exactly the
order the frozen Phase 20 ``HistoricalReplay`` composes them, and publishes
exactly what the frozen Phase 17 engine publishes:
``SignalEngineAnalyzer.update(EligibilitySnapshot) -> SignalSnapshot``. No
analyzer is modified, subclassed, re-implemented, or bypassed, and no private
replay helper (such as the offline ``_detection_chain``) is imported.

Higher timeframes
-----------------

The frozen ``MTFAnalyzer`` receives its higher-timeframe OTE frames at
construction and gates them with its own internal availability cursor. Live
HTF candles therefore arrive by deterministic reconstruction: every higher
timeframe's candle history is (re)materialized into OTE frames through the
same public Phase 3-9 analyzers the offline replay uses, and whenever a new
HTF candle completes, the MTF join and everything downstream of it are
rebuilt and the retained primary OTE frames are replayed through the fresh
chain. Because MTF gating consumes exactly the frames available at each
primary candle's open, the reconstruction is identical to having held the
full HTF history from the start — ``tests/live/test_runtime.py`` proves this
frame-for-frame against ``HistoricalReplay``/``SeriesFrameSource``.

The runtime holds no ledger, no persistence, no delivery, no network, and no
clock: it turns one closed primary candle into at most one published
``SignalSnapshot``, deterministically. It is not a strategy and not advice;
it never emits a short, an entry, or an order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.displacement.analyzer import DisplacementAnalyzer
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.analyzer import FVGAnalyzer
from smcsignal.analysis.halal_filter.analyzer import HalalFilterAnalyzer
from smcsignal.analysis.liquidity.analyzer import LiquidityAnalyzer
from smcsignal.analysis.mtf.analyzer import MTFAnalyzer
from smcsignal.analysis.mtf.config import MTFConfig
from smcsignal.analysis.order_blocks.analyzer import OrderBlockAnalyzer
from smcsignal.analysis.ote.analyzer import OTEAnalyzer
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.premium_discount.analyzer import PDAnalyzer
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.setup_quality.analyzer import SetupQualityAnalyzer
from smcsignal.analysis.signal_eligibility.analyzer import SignalEligibilityAnalyzer
from smcsignal.analysis.signal_engine.analyzer import SignalEngineAnalyzer
from smcsignal.analysis.signal_engine.models import SignalSnapshot
from smcsignal.data.models import OHLCV


def _validated_history(history: Iterable[OHLCV], name: str) -> tuple[OHLCV, ...]:
    candles = tuple(history)
    previous: OHLCV | None = None
    for candle in candles:
        if not isinstance(candle, OHLCV):
            raise AnalysisInputError(f"{name} must contain only OHLCV candles")
        if previous is not None and candle.timestamp <= previous.timestamp:
            raise AnalysisInputError(f"{name} must be chronological and unique")
        previous = candle
    return candles


def _htf_ote_frames(
    series: SeriesProvenance,
    candles: tuple[OHLCV, ...],
    configuration: BacktestConfiguration,
) -> tuple[OTESnapshot, ...]:
    """Materialize one higher timeframe's OTE frames through public analyzers.

    This is the live composition of the same public Phase 3-9 analyzers the
    frozen offline replay composes for its MTF join — written here against the
    public API only; the offline replay's private helpers are never imported.
    """

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
    frames: list[OTESnapshot] = []
    for candle in candles:
        a = liquidity.update(candle)
        b = displacement.update(a)
        c = fvg.update(b)
        d = order_blocks.update(c)
        e = premium_discount.update(d)
        frames.append(ote.update(e))
    return tuple(frames)


class LiveRuntime:
    """The frozen Phase 3-17 publication chain, fed one closed candle at a time.

    ``warm_up`` replays a historical closed-candle prefix (identical to the
    equivalent repeated ``update`` calls); ``update`` extends the chain with
    one new closed candle; ``extend_higher`` records one newly completed
    higher-timeframe candle for the deterministic MTF reconstruction. The
    published frames are real ``SignalSnapshot`` values — nothing wrapped,
    modified, or reinterpreted.
    """

    def __init__(
        self,
        configuration: BacktestConfiguration,
        *,
        series: SeriesProvenance,
        higher_candles: Mapping[str, Iterable[OHLCV]] | None = None,
    ) -> None:
        if not isinstance(configuration, BacktestConfiguration):
            raise AnalysisInputError("live runtime requires a BacktestConfiguration")
        if not isinstance(series, SeriesProvenance):
            raise AnalysisInputError("live runtime requires a SeriesProvenance")
        mtf_config = MTFConfig(
            primary_timeframe=series.timeframe,
            higher_timeframes=configuration.mtf.higher_timeframes,
            availability_policy=configuration.mtf.availability_policy,
        )
        supplied = {} if higher_candles is None else dict(higher_candles)
        if set(supplied) != set(mtf_config.higher_timeframes):
            raise AnalysisInputError(
                "higher_candles must supply exactly the configured higher timeframes: "
                + ", ".join(mtf_config.higher_timeframes)
            )
        self._configuration = configuration
        self._series = series
        self._mtf_config = mtf_config
        self._higher_candles = {
            timeframe: _validated_history(supplied[timeframe], f"higher_candles[{timeframe}]")
            for timeframe in mtf_config.higher_timeframes
        }
        self._liquidity = LiquidityAnalyzer(
            series=series, config=configuration.liquidity, analysis_config=configuration.analysis
        )
        self._displacement = DisplacementAnalyzer(
            configuration.displacement, price_unit=configuration.liquidity.price_unit
        )
        self._fvg = FVGAnalyzer(configuration.fvg)
        self._order_blocks = OrderBlockAnalyzer(configuration.order_blocks)
        self._premium_discount = PDAnalyzer(configuration.premium_discount)
        self._ote = OTEAnalyzer(configuration.ote)
        self._ote_frames: list[OTESnapshot] = []
        self._higher_dirty = True
        self._frame_count = 0
        self._latest: SignalSnapshot | None = None
        self._build_downstream()

    # -- frozen chain construction (mirrors the offline replay wiring) --------

    def _build_downstream(self) -> None:
        """(Re)build the MTF join and everything downstream of it, deterministically."""

        higher_frames = {
            timeframe: _htf_ote_frames(
                _htf_series(self._series, timeframe),
                self._higher_candles[timeframe],
                self._configuration,
            )
            for timeframe in self._mtf_config.higher_timeframes
        }
        self._mtf = MTFAnalyzer(self._mtf_config, higher=higher_frames)
        self._halal = HalalFilterAnalyzer(self._configuration.halal_filter)
        self._quality = SetupQualityAnalyzer(self._configuration.setup_quality)
        self._eligibility = SignalEligibilityAnalyzer(self._configuration.signal_eligibility)
        self._engine = SignalEngineAnalyzer(self._configuration.signal_engine)
        for frame in self._ote_frames:
            self._advance_downstream(frame)
        self._higher_dirty = False

    def _advance_downstream(self, frame: OTESnapshot) -> SignalSnapshot:
        g = self._mtf.update(frame)
        h = self._halal.update(g)
        i = self._quality.update(h)
        j = self._eligibility.update(i)
        return self._engine.update(j)

    def _push_primary(self, candle: OHLCV) -> OTESnapshot:
        if not isinstance(candle, OHLCV):
            raise AnalysisInputError("live runtime consumes OHLCV candles")
        a = self._liquidity.update(candle)
        b = self._displacement.update(a)
        c = self._fvg.update(b)
        d = self._order_blocks.update(c)
        e = self._premium_discount.update(d)
        frame = self._ote.update(e)
        self._ote_frames.append(frame)
        return frame

    # -- public API ------------------------------------------------------------

    def warm_up(self, candles: Iterable[OHLCV]) -> tuple[SignalSnapshot, ...]:
        """Replay a historical closed-candle prefix; identical to repeated updates."""

        return tuple(self.update(candle) for candle in candles)

    def update(self, candle: OHLCV) -> SignalSnapshot:
        """Consume one new closed primary candle; publish its real signal frame."""

        frame = self._push_primary(candle)
        if self._higher_dirty:
            self._build_downstream()
            snapshot = self._engine.latest
            if snapshot is None:  # pragma: no cover - engine always has a frame here
                raise AnalysisInputError("reconstructed chain published no frame")
        else:
            snapshot = self._advance_downstream(frame)
        self._frame_count += 1
        self._latest = snapshot
        return snapshot

    def extend_higher(self, timeframe: str, candle: OHLCV) -> None:
        """Record one newly completed higher-timeframe candle.

        The extension takes effect on the next ``update`` through the
        deterministic downstream reconstruction; the MTF availability cursor
        still decides exactly when the new context becomes eligible, so a
        completed HTF candle can never leak into an earlier primary candle.
        """

        if timeframe not in self._higher_candles:
            raise AnalysisInputError(f"untracked higher timeframe: {timeframe}")
        if not isinstance(candle, OHLCV):
            raise AnalysisInputError("extend_higher consumes OHLCV candles")
        history = self._higher_candles[timeframe]
        if history and candle.timestamp <= history[-1].timestamp:
            raise AnalysisInputError(
                f"higher-timeframe candles for {timeframe} must be chronological and unique"
            )
        self._higher_candles[timeframe] = (*history, candle)
        self._higher_dirty = True

    # -- introspection -----------------------------------------------------------

    @property
    def configuration(self) -> BacktestConfiguration:
        """The frozen pipeline configuration the chain runs under."""

        return self._configuration

    @property
    def series(self) -> SeriesProvenance:
        """The live series identity of the primary chain."""

        return self._series

    @property
    def mtf_config(self) -> MTFConfig:
        """The effective MTF configuration (primary timeframe bound at construction)."""

        return self._mtf_config

    @property
    def processed_count(self) -> int:
        """How many primary candles have been processed so far."""

        return self._frame_count

    @property
    def latest(self) -> SignalSnapshot | None:
        """The most recently published frame, if any."""

        return self._latest

    @property
    def higher_candles(self) -> Mapping[str, tuple[OHLCV, ...]]:
        """Read-only copies of the retained higher-timeframe histories."""

        return {timeframe: candles for timeframe, candles in self._higher_candles.items()}


def _htf_series(primary: SeriesProvenance, timeframe: str) -> SeriesProvenance:
    return SeriesProvenance(
        primary.symbol, timeframe, primary.venue, primary.provider, primary.dataset_id
    )
