"""Attribute published BUY_SIGNALs from existing nested facts without lookahead."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.setup_attribution.calculation import build_attribution
from smcsignal.analysis.setup_attribution.config import SetupAttributionConfig
from smcsignal.analysis.setup_attribution.evidence import (
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.setup_attribution.models import (
    AttributionSnapshot,
    current_observation,
)
from smcsignal.analysis.signal_engine.config import SignalEngineConfig
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus


class SetupAttributionAnalyzer:
    """Each output projects one signal frame's nested facts onto label taxonomy.

    BUY_SIGNAL frames carry exactly one attribution profile; other frames carry
    none. Attribution never reads outcomes or future candles, never changes
    signal decisions, and commits state only after all construction succeeds.
    """

    def __init__(self, config: SetupAttributionConfig | None = None) -> None:
        if config is not None and not isinstance(config, SetupAttributionConfig):
            raise AnalysisConfigurationError("config must be SetupAttributionConfig")
        self._config = config if config is not None else SetupAttributionConfig()
        self._latest: AttributionSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._attributed: tuple[str, ...] = ()
        self._signal_settings: SignalEngineConfig | None = None

    @property
    def config(self) -> SetupAttributionConfig:
        return self._config

    @property
    def latest(self) -> AttributionSnapshot | None:
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

    @property
    def attributed_signal_ids(self) -> tuple[str, ...]:
        """Read-only creation-ordered signal ids that carry attribution."""

        return self._attributed

    def update(self, frame: SignalSnapshot) -> AttributionSnapshot:
        """Consume one signal frame and publish its attribution delta."""

        if not isinstance(frame, SignalSnapshot):
            raise AnalysisInputError(
                "setup attribution requires existing SignalSnapshot frames starting at zero "
                "in consecutive unique order"
            )
        new_signal = frame.status is SignalStatus.BUY_SIGNAL
        if new_signal and frame.signal_id in self._attributed:
            raise AnalysisInputError("each BUY_SIGNAL is attributed at most once")
        if current_observation(frame).reference.candle_index != self._count:
            raise AnalysisInputError(
                "setup attribution requires existing SignalSnapshot frames starting at zero "
                "in consecutive unique order"
            )
        observation = current_observation(frame)
        series = frame.provenance.series
        if self._latest is not None:
            prior = current_observation(self._latest.upstream)
            if series != self._latest.upstream.provenance.series:
                raise AnalysisInputError("setup attribution series cannot change during a replay")
            if frame.provenance.available_at < self._latest.upstream.provenance.available_at:
                raise AnalysisInputError("setup attribution availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError(
                    "setup attribution candles must be chronological and unique"
                )
        if self._signal_settings is not None and frame.settings != self._signal_settings:
            raise AnalysisInputError("setup attribution requires one signal engine configuration")
        artifact = (
            self._artifact if self._artifact is not None else configuration_artifact(self.config)
        )
        config_hash = sha256(artifact).hexdigest()
        attribution = build_attribution(frame, self.config, config_hash) if new_signal else None
        result = AttributionSnapshot(
            self.config,
            frame,
            attribution,
            snapshot_provenance(frame, attribution, config_hash),
        )
        if new_signal:
            self._attributed = (*self._attributed, frame.signal_id)
        self._signal_settings = frame.settings
        self._latest, self._artifact = result, artifact
        self._count += 1
        return result


def analyze_setup_attribution(
    frames: Iterable[SignalSnapshot], config: SetupAttributionConfig | None = None
) -> tuple[AttributionSnapshot, ...]:
    """Sequential batch replay; identical to streaming and chunked updates."""

    analyzer = SetupAttributionAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of SignalSnapshot records") from exc
    return tuple(analyzer.update(frame) for frame in iterator)
