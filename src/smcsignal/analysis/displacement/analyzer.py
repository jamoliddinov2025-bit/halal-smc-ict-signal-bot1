"""Frame-native displacement: consume, never rerun, the approved Phase 4 layer."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.displacement.config import DisplacementConfig
from smcsignal.analysis.displacement.evidence import (
    build_atr,
    build_event,
    configuration_artifacts,
    displacement_provenance,
)
from smcsignal.analysis.displacement.models import (
    DisplacementMetrics,
    DisplacementSnapshot,
    qualifies,
)
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.liquidity.models import LiquiditySnapshot, ObservedCandle, SweepEvent
from smcsignal.analysis.provenance import SeriesProvenance, _text


class DisplacementAnalyzer:
    """One fixed-origin upstream series; a rejected frame changes no local state."""

    def __init__(self, config: DisplacementConfig | None = None, *, price_unit: str) -> None:
        if config is not None and not isinstance(config, DisplacementConfig):
            raise AnalysisConfigurationError("config must be DisplacementConfig")
        try:
            _text(price_unit, "price_unit")
        except AnalysisInputError as exc:
            raise AnalysisConfigurationError(str(exc)) from exc
        self._config = config if config is not None else DisplacementConfig()
        self._price_unit = price_unit
        self._artifacts = configuration_artifacts(self.config, price_unit)
        self._config_hash, self._atr_config_hash = (
            sha256(data).hexdigest() for data in self._artifacts
        )
        self._observations: tuple[ObservedCandle, ...] = ()
        self._sweeps: tuple[SweepEvent, ...] = ()
        self._latest: DisplacementSnapshot | None = None
        self._count = 0
        self._liquidity_hash: str | None = None

    @property
    def config(self) -> DisplacementConfig:
        return self._config

    @property
    def price_unit(self) -> str:
        return self._price_unit

    @property
    def configuration_artifact(self) -> bytes:
        return self._artifacts[0]

    @property
    def atr_configuration_artifact(self) -> bytes:
        return self._artifacts[1]

    @property
    def series(self) -> SeriesProvenance | None:
        return self._latest.provenance.series if self._latest is not None else None

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def latest(self) -> DisplacementSnapshot | None:
        return self._latest

    def _validate(self, frame: LiquiditySnapshot) -> str | None:
        if not isinstance(frame, LiquiditySnapshot):
            raise AnalysisInputError(
                "displacement requires an existing LiquiditySnapshot, not raw/recomputed data"
            )
        observation, meta = frame.context.observation, frame.context.provenance
        if observation.reference.candle_index != self._count:
            raise AnalysisInputError(
                "upstream frames must start at zero and be consecutive, unique observations"
            )
        if self._latest is not None:
            previous = self._latest.liquidity.context
            if (
                meta.series != previous.provenance.series
                or meta.configuration_hash != previous.provenance.configuration_hash
                or previous.provenance.as_reference() not in meta.dependencies
                or previous.observation.reference.closed_at > observation.reference.opened_at
                or previous.provenance.available_at > observation.available_at
            ):
                raise AnalysisInputError(
                    "upstream series, configuration, history, or availability is discontinuous"
                )
        liquidity_hash = self._liquidity_hash
        for pool in (*frame.pool_updates, *(e.pool for e in frame.sweeps)):
            if pool.settings.price_unit != self.price_unit:
                raise AnalysisInputError("upstream liquidity and displacement price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError(
                    "upstream liquidity configuration cannot change during a replay"
                )
            liquidity_hash = pool.provenance.configuration_hash
        return liquidity_hash

    def update(self, frame: LiquiditySnapshot) -> DisplacementSnapshot:
        """Classify t from the ATR published through t-1, then publish ATR(t).

        Sweep association is optional and direction-neutral. Current-candle
        sweeps are cached only AFTER classification, never attached retroactively.
        """
        liquidity_hash = self._validate(frame)
        observation, context = frame.context.observation, frame.context
        period = self.config.atr_period
        reference = self._latest.atr_current if self._latest is not None else None
        metrics = DisplacementMetrics(observation, reference)
        history = tuple(
            s
            for s in self._sweeps
            if self._count - s.breach.reference.candle_index <= self.config.sweep_lookback_bars
        )
        eligible = tuple(s for s in history if s.confirmed_at <= observation.reference.opened_at)
        newest = max((s.breach.reference.candle_index for s in eligible), default=-1)
        preceding = tuple(s for s in eligible if s.breach.reference.candle_index == newest)
        events = (
            (
                build_event(
                    metrics, context, preceding, self.config, self.price_unit, self._config_hash
                ),
            )
            if qualifies(metrics, self.config)
            else ()
        )

        # The classified candle enters ATR only for the next candidate.
        observations = (*self._observations, observation)[-(period + 1) :]
        current_atr = (
            build_atr(observations, context, period, self._atr_config_hash)
            if len(observations) == period + 1
            else None
        )
        extra = (
            (current_atr.provenance.as_reference(),) if current_atr is not None else ()
        ) + tuple(e.provenance.as_reference() for e in events)
        result = DisplacementSnapshot(
            liquidity=frame,
            settings=self.config,
            price_unit=self.price_unit,
            metrics=metrics,
            atr_reference=reference,
            atr_current=current_atr,
            preceding_sweeps=preceding,
            events=events,
            provenance=displacement_provenance(
                producer="displacement-frame",
                context=context,
                config_hash=self._config_hash,
                metrics=metrics,
                sweeps=preceding,
                extra=extra,
            ),
        )
        # Commit after all validation, arithmetic, model creation, and identity work succeeds.
        self._observations = observations
        self._sweeps = (*history, *frame.sweeps) if self.config.sweep_lookback_bars else ()
        self._latest, self._liquidity_hash = result, liquidity_hash
        self._count += 1
        return result


def analyze_displacement(
    frames: Iterable[LiquiditySnapshot],
    config: DisplacementConfig | None = None,
    *,
    price_unit: str,
) -> tuple[DisplacementSnapshot, ...]:
    """The same update loop over existing Phase 4 frames; no second upstream engine."""
    engine = DisplacementAnalyzer(config, price_unit=price_unit)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of LiquiditySnapshot records") from exc
    return tuple(engine.update(frame) for frame in iterator)
