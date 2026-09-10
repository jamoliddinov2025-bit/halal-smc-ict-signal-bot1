"""Causal pool clustering, first-breach retirement, and same-candle sweeps."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import datetime
from hashlib import sha256

from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.liquidity.config import LiquidityConfig, price_band
from smcsignal.analysis.liquidity.evidence import (
    canonical_bytes,
    configuration_artifact,
    digest,
    prefix_seed,
    provenance,
)
from smcsignal.analysis.liquidity.models import (
    InvalidationReason,
    LiquidityPool,
    LiquiditySide,
    LiquiditySnapshot,
    ObservedCandle,
    PoolStatus,
    StructureContext,
    SweepEvent,
    SwingEvidence,
    _breached,
    _failure,
)
from smcsignal.analysis.liquidity.time import candle_close_time
from smcsignal.analysis.models import SwingKind
from smcsignal.analysis.provenance import CandleReference, SeriesProvenance
from smcsignal.analysis.structure import MarketStructureAnalyzer
from smcsignal.analysis.swings import _candles
from smcsignal.data import OHLCV
from smcsignal.data.config import SUPPORTED_TIMEFRAMES


class LiquidityAnalyzer:
    """One explicit series/configuration per closed-candle replay; no I/O."""

    def __init__(
        self,
        *,
        series: SeriesProvenance,
        config: LiquidityConfig,
        analysis_config: AnalysisConfig | None = None,
    ) -> None:
        if not isinstance(series, SeriesProvenance) or series.timeframe not in SUPPORTED_TIMEFRAMES:
            raise AnalysisConfigurationError(
                "liquidity requires a series with a supported timeframe"
            )
        if not isinstance(config, LiquidityConfig):
            raise AnalysisConfigurationError("config must be LiquidityConfig")
        self._structure = MarketStructureAnalyzer(analysis_config)
        self._series, self._config = series, config
        self._artifact = configuration_artifact(self.analysis_config, config)
        self._configuration_hash = sha256(self._artifact).hexdigest()
        self._structure_hash = digest(
            {"methodology": "structure-v1", "analysis": self.analysis_config}
        )
        self._hasher = sha256(prefix_seed(series))
        self._window: deque[ObservedCandle] = deque(maxlen=self.analysis_config.fractal_length)
        self._active: dict[str, LiquidityPool] = {}
        self._latest: LiquiditySnapshot | None = None
        self._count = 0

    @property
    def series(self) -> SeriesProvenance:
        return self._series

    @property
    def config(self) -> LiquidityConfig:
        return self._config

    @property
    def analysis_config(self) -> AnalysisConfig:
        return self._structure.config

    @property
    def configuration_artifact(self) -> bytes:
        """Archive these exact bytes under SHA-256 to resolve the configuration hash."""
        return self._artifact

    @property
    def structure_configuration_artifact(self) -> bytes:
        return canonical_bytes({"methodology": "structure-v1", "analysis": self.analysis_config})

    @property
    def input_prefix_hash(self) -> str:
        return self._hasher.hexdigest()

    @property
    def active_pools(self) -> tuple[LiquidityPool, ...]:
        """Read-only current state in pool-creation order, not a quality ranking."""
        return tuple(self._active.values())

    @property
    def latest(self) -> LiquiditySnapshot | None:
        return self._latest

    @property
    def processed_count(self) -> int:
        return self._count

    def update(self, candle: OHLCV, *, available_at: datetime | None = None) -> LiquiditySnapshot:
        """Default availability is bar close; explicit arrival times must not rewind.

        Validate all external inputs and numeric band feasibility before changing
        any stream state. New confirmations cannot justify this candle's sweep.
        """
        if not isinstance(candle, OHLCV):
            raise AnalysisInputError("liquidity requires canonical OHLCV candles")
        closed_at = candle_close_time(candle.timestamp, self.series.timeframe)
        observation = ObservedCandle(
            candle,
            CandleReference(self.series, self._count, candle.timestamp, closed_at),
            closed_at if available_at is None else available_at,
        )
        before = self._latest.context if self._latest is not None else None
        if before is not None:
            if candle.timestamp < before.observation.reference.closed_at:
                raise AnalysisInputError(
                    "candles must be chronological, unique, and nonoverlapping"
                )
            if observation.available_at < before.provenance.available_at:
                raise AnalysisInputError("observation availability must not rewind")
        price_band(candle.high, self.config)
        price_band(candle.low, self.config)
        encoded = canonical_bytes(observation)
        next_hasher = self._hasher.copy()
        next_hasher.update(len(encoded).to_bytes(8, "big") + encoded)
        prefix_hash = next_hasher.hexdigest()

        structure = self._structure.update(candle)
        self._window.append(observation)
        window = tuple(self._window)
        confirmed = tuple(
            SwingEvidence(
                swing,
                window,
                provenance(
                    series=self.series,
                    producer="confirmed-swing",
                    configuration_hash=self._structure_hash,
                    input_prefix_hash=prefix_hash,
                    available_at=observation.available_at,
                    key=swing,
                    source_candles=tuple(c.reference for c in window),
                ),
            )
            for swing in structure.confirmed_swings
        )
        context = StructureContext(
            observation,
            structure,
            provenance(
                series=self.series,
                producer="structure-context",
                configuration_hash=self._structure_hash,
                input_prefix_hash=prefix_hash,
                available_at=observation.available_at,
                key=structure,
                source_candles=(observation.reference,),
                dependencies=(
                    tuple([before.provenance.as_reference()]) if before is not None else ()
                )
                + tuple(s.provenance.as_reference() for s in confirmed),
            ),
        )
        updates: list[LiquidityPool] = []
        events: list[SweepEvent] = []
        # Retire ALL previously active pools hit by this bar, before adding touches.
        for pool in tuple(self._active.values()):
            if not _breached(pool.side, pool.lower_bound, pool.upper_bound, candle):
                continue
            assert before is not None
            reason = _failure(
                pool.side,
                pool.lower_bound,
                pool.upper_bound,
                pool.provenance.available_at,
                before.observation,
                observation,
            )
            sweep = None
            if reason is None:
                sweep = SweepEvent(
                    pool,
                    before.observation,
                    observation,
                    before,
                    context,
                    provenance(
                        series=self.series,
                        producer="liquidity-sweep",
                        configuration_hash=self._configuration_hash,
                        input_prefix_hash=prefix_hash,
                        available_at=observation.available_at,
                        key={
                            "pool": pool.provenance.evidence_id,
                            "context_before": before.provenance.evidence_id,
                            "context": context.provenance.evidence_id,
                        },
                        source_candles=(before.observation.reference, observation.reference),
                        dependencies=(
                            pool.provenance.as_reference(),
                            before.provenance.as_reference(),
                            context.provenance.as_reference(),
                        ),
                    ),
                )
                events.append(sweep)
            updates.append(
                self._pool(
                    pool.pool_id,
                    pool.members,
                    context,
                    previous=pool,
                    status=PoolStatus.SWEPT if sweep is not None else PoolStatus.INVALIDATED,
                    reason=reason,
                    sweep=sweep,
                    previous_candle=before.observation,
                )
            )
            del self._active[pool.pool_id]

        for member in confirmed:
            side = (
                LiquiditySide.BUY_SIDE
                if member.swing.kind == SwingKind.HIGH
                else LiquiditySide.SELL_SIDE
            )
            # Deterministic oldest matching anchor; no transitive cluster merging.
            match = next(
                (
                    pool
                    for pool in self._active.values()
                    if pool.side == side
                    and pool.lower_bound <= member.swing.price <= pool.upper_bound
                ),
                None,
            )
            members: tuple[SwingEvidence, ...]
            if match is None:
                pool_id = "pool:" + digest(
                    {
                        "configuration": self._configuration_hash,
                        "first_member": member.provenance.evidence_id,
                    }
                )
                members = (member,)
            else:
                pool_id, members = match.pool_id, (*match.members, member)
            pool = self._pool(pool_id, members, context, previous=match)
            self._active[pool_id] = pool
            updates.append(pool)

        result = LiquiditySnapshot(context, confirmed, tuple(updates), tuple(events))
        self._latest, self._hasher = result, next_hasher
        self._count += 1
        return result

    def _pool(
        self,
        pool_id: str,
        members: tuple[SwingEvidence, ...],
        context: StructureContext,
        *,
        previous: LiquidityPool | None,
        status: PoolStatus = PoolStatus.ACTIVE,
        reason: InvalidationReason | None = None,
        sweep: SweepEvent | None = None,
        previous_candle: ObservedCandle | None = None,
    ) -> LiquidityPool:
        previous_ref = previous.provenance.as_reference() if previous is not None else None
        sweep_ref = sweep.provenance.as_reference() if sweep is not None else None
        dependencies = (
            context.provenance.as_reference(),
            *(m.provenance.as_reference() for m in members),
        )
        if previous_ref is not None:
            dependencies += (previous_ref,)
        if sweep_ref is not None:
            dependencies += (sweep_ref,)
        observation = context.observation
        return LiquidityPool(
            pool_id=pool_id,
            settings=self.config,
            members=members,
            status=status,
            context=context,
            previous_snapshot=previous_ref,
            transition=observation if previous_candle is not None else None,
            previous_candle=previous_candle,
            invalidation_reason=reason,
            sweep_reference=sweep_ref,
            provenance=provenance(
                series=self.series,
                producer="liquidity-pool",
                configuration_hash=self._configuration_hash,
                input_prefix_hash=context.provenance.input_prefix_hash,
                available_at=observation.available_at,
                key={
                    "pool_id": pool_id,
                    "members": tuple(m.provenance.evidence_id for m in members),
                    "status": status,
                    "reason": reason,
                },
                source_candles=((previous_candle.reference,) if previous_candle is not None else ())
                + (observation.reference,),
                dependencies=dependencies,
            ),
        )


def analyze_liquidity(
    candles: Iterable[OHLCV],
    *,
    series: SeriesProvenance,
    config: LiquidityConfig,
    analysis_config: AnalysisConfig | None = None,
) -> tuple[LiquiditySnapshot, ...]:
    """Replay historical closed bars, assuming availability at each bar's close."""
    analyzer = LiquidityAnalyzer(series=series, config=config, analysis_config=analysis_config)
    return tuple(analyzer.update(candle) for candle in _candles(candles))
