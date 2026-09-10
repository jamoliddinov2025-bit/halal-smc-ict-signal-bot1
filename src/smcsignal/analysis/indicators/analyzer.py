"""Stream indicator context over existing displacement frames without lookahead."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.indicators.calculation import (
    EMACalculator,
    RSICalculator,
    VolumeAverageCalculator,
)
from smcsignal.analysis.indicators.config import IndicatorsConfig
from smcsignal.analysis.indicators.evidence import (
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.indicators.models import (
    IndicatorSnapshot,
    current_observation,
)
from smcsignal.analysis.provenance import SeriesProvenance


class IndicatorAnalyzer:
    """Each output adds supporting context for one closed candle.

    Indicators never generate, gate, or veto signals; no decision module reads
    these frames. ATR is the Phase 5 published value reused verbatim. State
    commits only after every check, value, and provenance construction succeeds.
    """

    def __init__(self, config: IndicatorsConfig | None = None) -> None:
        if config is not None and not isinstance(config, IndicatorsConfig):
            raise AnalysisConfigurationError("config must be IndicatorsConfig")
        self._config = config if config is not None else IndicatorsConfig()
        self._latest: IndicatorSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._ema_states = tuple(EMACalculator(period) for period in self._config.ema_periods)
        self._rsi_state = RSICalculator(self._config.rsi_period)
        self._volume_state = VolumeAverageCalculator(self._config.volume_average_period)

    @property
    def config(self) -> IndicatorsConfig:
        return self._config

    @property
    def latest(self) -> IndicatorSnapshot | None:
        return self._latest

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def series(self) -> SeriesProvenance | None:
        return self._latest.provenance.series if self._latest is not None else None

    @property
    def configuration_artifact(self) -> bytes | None:
        return self._artifact

    def update(self, frame: DisplacementSnapshot) -> IndicatorSnapshot:
        """Consume one displacement frame and publish its indicator context."""

        if not isinstance(frame, DisplacementSnapshot) or (
            current_observation(frame).reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "indicators require existing DisplacementSnapshot frames starting at zero "
                "in consecutive unique order"
            )
        observation = current_observation(frame)
        series = frame.provenance.series
        if self._latest is not None:
            prior = current_observation(self._latest.upstream)
            if series != self._latest.upstream.provenance.series:
                raise AnalysisInputError("indicator series cannot change during a replay")
            if frame.provenance.available_at < self._latest.upstream.provenance.available_at:
                raise AnalysisInputError("indicator availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError("indicator candles must be chronological and unique")
        artifact = (
            self._artifact if self._artifact is not None else configuration_artifact(self.config)
        )
        config_hash = sha256(artifact).hexdigest()
        candle = observation.candle
        ema_states = []
        ema_values = []
        for state in self._ema_states:
            state, value = state.update(candle.close)
            ema_states.append(state)
            ema_values.append(value)
        rsi_state, rsi = self._rsi_state.update(candle.close)
        volume_state, volume_average, volume_ratio = self._volume_state.update(candle.volume)
        atr = frame.atr_current.value if frame.atr_current is not None else None
        result = IndicatorSnapshot(
            settings=self.config,
            upstream=frame,
            ema_values=tuple(ema_values),
            rsi=rsi,
            atr=atr,
            volume=candle.volume,
            volume_average=volume_average,
            volume_ratio=volume_ratio,
            provenance=snapshot_provenance(
                frame,
                self.config,
                ema_values=tuple(ema_values),
                rsi=rsi,
                atr=atr,
                volume=candle.volume,
                volume_average=volume_average,
                volume_ratio=volume_ratio,
                config_hash=config_hash,
            ),
        )
        self._ema_states = tuple(ema_states)
        self._rsi_state = rsi_state
        self._volume_state = volume_state
        self._latest, self._artifact = result, artifact
        self._count += 1
        return result


def analyze_indicators(
    frames: Iterable[DisplacementSnapshot], config: IndicatorsConfig | None = None
) -> tuple[IndicatorSnapshot, ...]:
    """Sequential batch replay; identical to streaming and chunked updates."""

    analyzer = IndicatorAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError(
            "frames must be an iterable of DisplacementSnapshot records"
        ) from exc
    return tuple(analyzer.update(frame) for frame in iterator)
