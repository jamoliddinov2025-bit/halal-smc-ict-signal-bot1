"""Apply a frozen asset registry to existing MTF frames without lookahead."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.halal_filter.config import HalalFilterConfig, normalize_asset
from smcsignal.analysis.halal_filter.evidence import (
    build_decision,
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.halal_filter.models import HalalDecision, HalalSnapshot, current_observation
from smcsignal.analysis.mtf.models import MTFSnapshot
from smcsignal.analysis.provenance import SeriesProvenance


class HalalFilterAnalyzer:
    """Each output records the configured registry class of the current series."""

    def __init__(self, config: HalalFilterConfig | None = None) -> None:
        if config is not None and not isinstance(config, HalalFilterConfig):
            raise AnalysisConfigurationError("config must be HalalFilterConfig")
        self._config = config if config is not None else HalalFilterConfig()
        self._latest: HalalSnapshot | None = None
        self._decision: HalalDecision | None = None
        self._count = 0
        self._artifact: bytes | None = None

    @property
    def config(self) -> HalalFilterConfig:
        return self._config

    @property
    def latest(self) -> HalalSnapshot | None:
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

    def update(self, frame: MTFSnapshot) -> HalalSnapshot:
        if not isinstance(frame, MTFSnapshot) or (
            current_observation(frame).reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "halal filter requires existing MTF frames starting at zero "
                "in consecutive unique order"
            )
        observation = current_observation(frame)
        series = frame.provenance.series
        previous = self._latest.upstream if self._latest is not None else None
        if previous is not None:
            prior = current_observation(previous)
            if series != previous.provenance.series:
                raise AnalysisInputError("halal filter series cannot change during a replay")
            if frame.provenance.available_at < previous.provenance.available_at:
                raise AnalysisInputError("halal filter availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError("halal filter candles must be chronological and unique")
        artifact = (
            self._artifact if self._artifact is not None else configuration_artifact(self.config)
        )
        config_hash = sha256(artifact).hexdigest()
        if self._decision is None:
            decision = build_decision(
                self.config,
                series.symbol,
                series,
                frame.provenance.available_at,
                config_hash,
            )
        else:
            if normalize_asset(series.symbol, error=AnalysisInputError) != self._decision.symbol:
                raise AnalysisInputError("halal filter symbol cannot change during a replay")
            decision = self._decision
        result = HalalSnapshot(
            self.config, frame, decision, snapshot_provenance(frame, decision, config_hash)
        )
        self._latest, self._artifact, self._decision = result, artifact, decision
        self._count += 1
        return result


def analyze_halal(
    frames: Iterable[MTFSnapshot], config: HalalFilterConfig | None = None
) -> tuple[HalalSnapshot, ...]:
    engine = HalalFilterAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of MTFSnapshot records") from exc
    return tuple(engine.update(frame) for frame in iterator)
