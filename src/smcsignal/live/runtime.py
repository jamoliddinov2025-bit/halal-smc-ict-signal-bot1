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

Phase 35E: bounded ownership and deterministic rebuild
------------------------------------------------------

HTF OTE materialization is append-cached per timeframe: a completed HTF
candle extends the retained frame tuple through a live analyzer chain
instead of rematerializing the entire higher history on every rebuild. When
a newly completed HTF candle cannot be eligible for any already-processed
primary open (its ``available_at`` is strictly after the last processed
primary open — the common live case), the runtime fast-forwards the MTF
join by reconstructing ``MTFAnalyzer`` over the extended frame tuples and
restoring the documented cursor/published/count/latest fields, leaving the
already-equivalent downstream analyzers untouched; only the new primary
frame then advances. When the new HTF context *can* be eligible for a
processed primary open (true late arrival covering already-seen opens), the
full deterministic replay of every retained primary OTE frame runs exactly
as before. Either path is frame-for-frame equivalent to having held the full
HTF history from the start.

Explicit checkpoint export (Phase 35E) exposes the recovery state the
service persists — retained primary OTE frames (from which every primary
candle is recoverable), full higher-timeframe candles, the engine's
duplicate-publication fencing set, cursors, and the latest signal id — so
the service can bound its own raw windows without discarding state the
frozen Phase 26G ledger open or a later HTF rebuild still requires. The
fencing set is read from the engine's published-identity registry through a
documented persistence contract (the frozen engine exposes no public view of
that set, and no safe seam can be added inside the frozen analysis package).

The runtime holds no ledger, no persistence, no delivery, no network, and no
clock: it turns one closed primary candle into at most one published
``SignalSnapshot``, deterministically. It is not a strategy and not advice;
it never emits a short, an entry, or an order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True, slots=True)
class RuntimeCheckpointState:
    """Explicit Phase 35E export of one runtime's recovery-relevant state.

    This is the deliberate checkpoint seam — not accidental serialization of
    object internals. Every field is a plain primitive, frozen model, or
    tuple of frozen models with stable ordering:

    - ``frame_count`` / ``latest_signal_id`` — primary sequence cursors;
    - ``published_setups`` — duplicate-publication fencing (sorted);
    - ``primary_candles`` — full primary recovery history, extracted from the
      retained OTE frames (one frame per primary candle, in order);
    - ``higher_candles`` — full HTF recovery histories for rematerialization;
    - ``last_primary`` / ``last_higher`` — feed cursors;
    - ``higher_dirty`` — whether an HTF extension is pending reconstruction.
    """

    frame_count: int
    latest_signal_id: str | None
    published_setups: tuple[str, ...]
    primary_candles: tuple[OHLCV, ...]
    higher_candles: Mapping[str, tuple[OHLCV, ...]]
    last_primary: datetime | None
    last_higher: Mapping[str, datetime | None]
    higher_dirty: bool


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


def primary_candle_of(frame: OTESnapshot) -> OHLCV:
    """Recover the primary OHLCV one retained OTE frame was built from.

    Walks the public frozen frame chain (``OTESnapshot.upstream`` is the PD
    snapshot, which exposes its ``ObservedCandle``). Used by the Phase 35E
    checkpoint export so the service never keeps a second unbounded candle
    buffer when the retained frames already carry the full recovery history.
    """

    if not isinstance(frame, OTESnapshot):
        raise AnalysisInputError("primary_candle_of requires an OTESnapshot")
    return frame.upstream.observation.candle


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


@dataclass
class _HigherChain:
    """Live analyzer chain plus materialized frames for one higher timeframe."""

    liquidity: LiquidityAnalyzer
    displacement: DisplacementAnalyzer
    fvg: FVGAnalyzer
    order_blocks: OrderBlockAnalyzer
    premium_discount: PDAnalyzer
    ote: OTEAnalyzer
    frames: list[OTESnapshot]


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
        # Phase 35E: append-cached HTF materialization (chains stay alive so a
        # completed HTF candle is processed once, not on every rebuild).
        self._higher_chains: dict[str, _HigherChain] = {}
        self._mtf: MTFAnalyzer
        self._halal: HalalFilterAnalyzer
        self._quality: SetupQualityAnalyzer
        self._eligibility: SignalEligibilityAnalyzer
        self._engine: SignalEngineAnalyzer
        # Frame counts of the last downstream build — decides fast-forward.
        self._built_higher_counts: dict[str, int] = {
            timeframe: 0 for timeframe in mtf_config.higher_timeframes
        }
        self._build_downstream()

    # -- frozen chain construction (mirrors the offline replay wiring) --------

    def _materialize_higher_frames(self) -> dict[str, tuple[OTESnapshot, ...]]:
        """Return every HTF's OTE frames, extending live chains for new candles."""

        higher_frames: dict[str, tuple[OTESnapshot, ...]] = {}
        for timeframe in self._mtf_config.higher_timeframes:
            candles = self._higher_candles[timeframe]
            chain = self._higher_chains.get(timeframe)
            if chain is None:
                chain = self._new_higher_chain(timeframe)
                for candle in candles:
                    self._extend_higher_chain(chain, candle)
                self._higher_chains[timeframe] = chain
            else:
                for candle in candles[len(chain.frames) :]:
                    self._extend_higher_chain(chain, candle)
            higher_frames[timeframe] = tuple(chain.frames)
        return higher_frames

    def _new_higher_chain(self, timeframe: str) -> _HigherChain:
        series = _htf_series(self._series, timeframe)
        configuration = self._configuration
        return _HigherChain(
            liquidity=LiquidityAnalyzer(
                series=series,
                config=configuration.liquidity,
                analysis_config=configuration.analysis,
            ),
            displacement=DisplacementAnalyzer(
                configuration.displacement, price_unit=configuration.liquidity.price_unit
            ),
            fvg=FVGAnalyzer(configuration.fvg),
            order_blocks=OrderBlockAnalyzer(configuration.order_blocks),
            premium_discount=PDAnalyzer(configuration.premium_discount),
            ote=OTEAnalyzer(configuration.ote),
            frames=[],
        )

    @staticmethod
    def _extend_higher_chain(chain: _HigherChain, candle: OHLCV) -> None:
        a = chain.liquidity.update(candle)
        b = chain.displacement.update(a)
        c = chain.fvg.update(b)
        d = chain.order_blocks.update(c)
        e = chain.premium_discount.update(d)
        chain.frames.append(chain.ote.update(e))

    def _new_htf_affects_processed_primaries(
        self, higher_frames: Mapping[str, tuple[OTESnapshot, ...]]
    ) -> bool:
        """True when a newly materialized HTF frame may gate an already-processed open.

        "Already processed" means frames the current downstream chain has
        consumed (``mtf.processed_count``), not a primary frame that was just
        pushed and is about to be advanced.
        """

        processed = self._mtf.processed_count
        if processed <= 0 or processed > len(self._ote_frames):
            return False
        last_open = self._ote_frames[processed - 1].upstream.observation.reference.opened_at
        for timeframe, frames in higher_frames.items():
            already_built = self._built_higher_counts.get(timeframe, 0)
            for frame in frames[already_built:]:
                if frame.provenance.available_at <= last_open:
                    return True
        return False

    def _fast_forward_downstream(
        self, higher_frames: Mapping[str, tuple[OTESnapshot, ...]]
    ) -> None:
        """Rebuild only the MTF join over extended HTF frames; keep downstream.

        Documented Phase 35E persistence/restoration contract with the frozen
        ``MTFAnalyzer``: the analyzer receives its higher frames at
        construction and exposes no public mutation seam (the analysis package
        is frozen, so no safe seam can be added there). When new HTF frames
        cannot gate any already-processed primary open, the existing cursor,
        published-evidence, count, latest snapshot, symbol, and configuration
        artifact remain exactly correct for the extended frame tuples (the
        new frames sit at indices at-or-beyond the cursor and are not yet
        eligible). This method reconstructs ``MTFAnalyzer`` over the extended
        tuples and restores those six fields — the minimum state that
        determines future gating — then leaves halal/quality/eligibility/
        engine untouched because their inputs (past MTF outputs) are
        unchanged. The subsequent primary frame then advances through the
        restored join normally.
        """

        previous = self._mtf
        restored = MTFAnalyzer(self._mtf_config, higher=higher_frames)
        # Private-field restore is the explicit, documented Phase 35E seam for
        # this one frozen analyzer; every restored value is read from the
        # public predecessor instance and validated by the analyzer's own
        # update() on the very next frame.
        object.__setattr__(restored, "_cursor", dict(previous._cursor))  # noqa: SLF001
        object.__setattr__(
            restored,
            "_published",
            {tf: tuple(previous._published[tf]) for tf in self._mtf_config.higher_timeframes},  # noqa: SLF001
        )
        object.__setattr__(restored, "_latest", previous._latest)  # noqa: SLF001
        object.__setattr__(restored, "_count", previous._count)  # noqa: SLF001
        object.__setattr__(restored, "_artifact", previous._artifact)  # noqa: SLF001
        object.__setattr__(restored, "_symbol", previous._symbol)  # noqa: SLF001
        self._mtf = restored

    def _build_downstream(self) -> None:
        """(Re)build the MTF join and everything downstream of it, deterministically.

        Called when ``_higher_dirty`` is set and the current primary frame has
        already been pushed onto ``_ote_frames``. Fast-forward applies when the
        new HTF context cannot gate any already-processed primary open; the
        full path replays every retained primary OTE frame through a fresh
        chain — identical to holding the full HTF history from the start.
        """

        higher_frames = self._materialize_higher_frames()
        if self._can_fast_forward(higher_frames):
            self._fast_forward_downstream(higher_frames)
            self._built_higher_counts = {tf: len(f) for tf, f in higher_frames.items()}
            self._higher_dirty = False
            # The frame just pushed is not yet in the restored join (its count
            # is still the pre-push count); advance it now so engine.latest
            # matches the full-replay path.
            self._advance_downstream(self._ote_frames[-1])
            return
        self._mtf = MTFAnalyzer(self._mtf_config, higher=higher_frames)
        self._halal = HalalFilterAnalyzer(self._configuration.halal_filter)
        self._quality = SetupQualityAnalyzer(self._configuration.setup_quality)
        self._eligibility = SignalEligibilityAnalyzer(self._configuration.signal_eligibility)
        self._engine = SignalEngineAnalyzer(self._configuration.signal_engine)
        for frame in self._ote_frames:
            self._advance_downstream(frame)
        self._built_higher_counts = {tf: len(f) for tf, f in higher_frames.items()}
        self._higher_dirty = False

    def _can_fast_forward(self, higher_frames: Mapping[str, tuple[OTESnapshot, ...]]) -> bool:
        """Fast-forward only when downstream state already covers pre-push frames."""

        if self._latest is None or not self._ote_frames:
            return False
        if set(self._higher_chains) != set(self._mtf_config.higher_timeframes):
            return False
        # Downstream must have processed every OTE frame except the one just
        # pushed by update() (full-rebuild path includes it; fast path advances
        # it separately after restoring the join).
        expected_before_push = self._frame_count  # frames completed before this update
        if self._mtf.processed_count != expected_before_push:
            return False
        if self._engine is None or self._engine.processed_count != expected_before_push:
            return False
        if self._new_htf_affects_processed_primaries(higher_frames):
            return False
        return all(
            self._built_higher_counts.get(tf, 0) <= len(frames)
            for tf, frames in higher_frames.items()
        )

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

    def export_checkpoint_state(self) -> RuntimeCheckpointState:
        """Explicit Phase 35E state export for deterministic checkpointing.

        Never pickles object internals and never depends on addresses,
        unordered iteration, or serialization-time timestamps: candles are
        extracted from the retained OTE frames in order, HTF histories are
        copied as tuples, and the fencing set is sorted. The export is a pure
        read — the runtime continues unchanged.
        """

        primary_candles = tuple(primary_candle_of(frame) for frame in self._ote_frames)
        if len(primary_candles) != self._frame_count:
            raise AnalysisInputError(
                "runtime export invariant violated: one OTE frame per processed primary candle"
            )
        latest = self._latest
        # Documented persistence contract: the frozen engine keeps its
        # duplicate-fencing registry in ``_published`` and exposes no public
        # view; the live package is the sanctioned checkpoint owner and reads
        # it here so a restart can verify the registry was regenerated
        # identically. Nothing outside live/ touches this field.
        published = tuple(sorted(self._engine._published))  # noqa: SLF001
        higher = {timeframe: tuple(candles) for timeframe, candles in self._higher_candles.items()}
        return RuntimeCheckpointState(
            frame_count=self._frame_count,
            latest_signal_id=None if latest is None else latest.signal_id,
            published_setups=published,
            primary_candles=primary_candles,
            higher_candles=higher,
            last_primary=None if not primary_candles else primary_candles[-1].timestamp,
            last_higher={
                timeframe: (candles[-1].timestamp if candles else None)
                for timeframe, candles in higher.items()
            },
            higher_dirty=self._higher_dirty,
        )

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

    @property
    def higher_dirty(self) -> bool:
        """Whether an HTF extension is pending downstream reconstruction."""

        return self._higher_dirty

    @property
    def ote_frame_count(self) -> int:
        """Number of retained primary OTE frames (equals ``processed_count``)."""

        return len(self._ote_frames)

    @property
    def published_setups(self) -> tuple[str, ...]:
        """Sorted duplicate-fencing identities (documented engine registry read)."""

        return tuple(sorted(self._engine._published))  # noqa: SLF001


def _htf_series(primary: SeriesProvenance, timeframe: str) -> SeriesProvenance:
    return SeriesProvenance(
        primary.symbol, timeframe, primary.venue, primary.provider, primary.dataset_id
    )
