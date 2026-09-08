"""Apply frozen spot mapping to existing EligibilitySnapshot frames without lookahead."""

from __future__ import annotations

from collections.abc import Iterable

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.signal_eligibility.models import EligibilitySnapshot
from smcsignal.analysis.signal_engine.calculation import build_signal_snapshot
from smcsignal.analysis.signal_engine.config import SignalEngineConfig
from smcsignal.analysis.signal_engine.evidence import configuration_artifact
from smcsignal.analysis.signal_engine.models import (
    SignalSnapshot,
    SignalStatus,
    current_observation,
)


class SignalEngineAnalyzer:
    """Each output maps already-published eligibility to a spot publication."""

    def __init__(self, config: SignalEngineConfig | None = None) -> None:
        if config is not None and not isinstance(config, SignalEngineConfig):
            raise AnalysisConfigurationError("config must be SignalEngineConfig")
        self._config = config if config is not None else SignalEngineConfig()
        self._latest: SignalSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._published: set[str] = set()

    @property
    def config(self) -> SignalEngineConfig:
        return self._config

    @property
    def latest(self) -> SignalSnapshot | None:
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

    def update(self, frame: EligibilitySnapshot) -> SignalSnapshot:
        if not isinstance(frame, EligibilitySnapshot) or (
            current_observation(frame).reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "signal engine requires existing EligibilitySnapshot frames starting at zero "
                "in consecutive unique order"
            )
        observation = current_observation(frame)
        series = frame.provenance.series
        previous = self._latest.upstream if self._latest is not None else None
        if previous is not None:
            prior = current_observation(previous)
            if series != previous.provenance.series:
                raise AnalysisInputError("signal engine series cannot change during a replay")
            if frame.provenance.available_at < previous.provenance.available_at:
                raise AnalysisInputError("signal engine availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError("signal engine candles must be chronological and unique")
        artifact = (
            self._artifact if self._artifact is not None else configuration_artifact(self.config)
        )
        result = build_signal_snapshot(frame, self.config, seen_setups=self._published)
        if result.status is SignalStatus.BUY_SIGNAL:
            self._published.add(result.setup_identity)
        self._latest, self._artifact = result, artifact
        self._count += 1
        return result


def analyze_signal_engine(
    frames: Iterable[EligibilitySnapshot], config: SignalEngineConfig | None = None
) -> tuple[SignalSnapshot, ...]:
    engine = SignalEngineAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError(
            "frames must be an iterable of EligibilitySnapshot records"
        ) from exc
    return tuple(engine.update(frame) for frame in iterator)
