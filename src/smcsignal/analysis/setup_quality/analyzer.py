"""Score existing HalalSnapshot frames without lookahead or rerunning detectors."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.halal_filter.models import HalalSnapshot
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.setup_quality.calculation import breakdown_for
from smcsignal.analysis.setup_quality.config import SetupQualityConfig
from smcsignal.analysis.setup_quality.evidence import (
    configuration_artifact,
    score_provenance,
    snapshot_provenance,
)
from smcsignal.analysis.setup_quality.models import (
    ScoreSnapshot,
    SetupQualityScore,
    current_observation,
)


class SetupQualityAnalyzer:
    """Each output records the integer quality of already-published nested facts."""

    def __init__(self, config: SetupQualityConfig | None = None) -> None:
        if config is not None and not isinstance(config, SetupQualityConfig):
            raise AnalysisConfigurationError("config must be SetupQualityConfig")
        self._config = config if config is not None else SetupQualityConfig()
        self._latest: ScoreSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None

    @property
    def config(self) -> SetupQualityConfig:
        return self._config

    @property
    def latest(self) -> ScoreSnapshot | None:
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

    def update(self, frame: HalalSnapshot) -> ScoreSnapshot:
        if not isinstance(frame, HalalSnapshot) or (
            current_observation(frame).reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "setup quality requires existing HalalSnapshot frames starting at zero "
                "in consecutive unique order"
            )
        observation = current_observation(frame)
        series = frame.provenance.series
        previous = self._latest.upstream if self._latest is not None else None
        if previous is not None:
            prior = current_observation(previous)
            if series != previous.provenance.series:
                raise AnalysisInputError("setup quality series cannot change during a replay")
            if frame.provenance.available_at < previous.provenance.available_at:
                raise AnalysisInputError("setup quality availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError("setup quality candles must be chronological and unique")
        artifact = (
            self._artifact if self._artifact is not None else configuration_artifact(self.config)
        )
        config_hash = sha256(artifact).hexdigest()
        breakdown = breakdown_for(frame)
        score = SetupQualityScore(
            self.config,
            breakdown,
            frame.eligible,
            score_provenance(frame, breakdown, frame.eligible, self.config, config_hash),
        )
        result = ScoreSnapshot(
            self.config, frame, score, snapshot_provenance(frame, score, config_hash)
        )
        self._latest, self._artifact = result, artifact
        self._count += 1
        return result


def analyze_setup_quality(
    frames: Iterable[HalalSnapshot], config: SetupQualityConfig | None = None
) -> tuple[ScoreSnapshot, ...]:
    engine = SetupQualityAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of HalalSnapshot records") from exc
    return tuple(engine.update(frame) for frame in iterator)
