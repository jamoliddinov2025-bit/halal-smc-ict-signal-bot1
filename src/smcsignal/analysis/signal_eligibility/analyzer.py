"""Apply frozen eligibility rules to existing ScoreSnapshot frames without lookahead."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.setup_quality.models import ScoreSnapshot
from smcsignal.analysis.signal_eligibility.calculation import evaluate
from smcsignal.analysis.signal_eligibility.config import SignalEligibilityConfig
from smcsignal.analysis.signal_eligibility.evidence import (
    configuration_artifact,
    decision_provenance,
    snapshot_provenance,
)
from smcsignal.analysis.signal_eligibility.models import (
    EligibilityDecision,
    EligibilitySnapshot,
    current_observation,
)


class SignalEligibilityAnalyzer:
    """Each output records whether already-published facts may feed a future engine."""

    def __init__(self, config: SignalEligibilityConfig | None = None) -> None:
        if config is not None and not isinstance(config, SignalEligibilityConfig):
            raise AnalysisConfigurationError("config must be SignalEligibilityConfig")
        self._config = config if config is not None else SignalEligibilityConfig()
        self._latest: EligibilitySnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None

    @property
    def config(self) -> SignalEligibilityConfig:
        return self._config

    @property
    def latest(self) -> EligibilitySnapshot | None:
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

    def update(self, frame: ScoreSnapshot) -> EligibilitySnapshot:
        if not isinstance(frame, ScoreSnapshot) or (
            current_observation(frame).reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "signal eligibility requires existing ScoreSnapshot frames starting at zero "
                "in consecutive unique order"
            )
        observation = current_observation(frame)
        series = frame.provenance.series
        previous = self._latest.upstream if self._latest is not None else None
        if previous is not None:
            prior = current_observation(previous)
            if series != previous.provenance.series:
                raise AnalysisInputError("signal eligibility series cannot change during a replay")
            if frame.provenance.available_at < previous.provenance.available_at:
                raise AnalysisInputError("signal eligibility availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError(
                    "signal eligibility candles must be chronological and unique"
                )
        artifact = (
            self._artifact if self._artifact is not None else configuration_artifact(self.config)
        )
        config_hash = sha256(artifact).hexdigest()
        judged = evaluate(frame)
        decision = EligibilityDecision(
            self.config,
            judged.eligibility,
            judged.reasons,
            judged.evidence,
            decision_provenance(
                frame, judged.eligibility, judged.reasons, judged.evidence, config_hash
            ),
        )
        result = EligibilitySnapshot(
            self.config, frame, decision, snapshot_provenance(frame, decision, config_hash)
        )
        self._latest, self._artifact = result, artifact
        self._count += 1
        return result


def analyze_signal_eligibility(
    frames: Iterable[ScoreSnapshot], config: SignalEligibilityConfig | None = None
) -> tuple[EligibilitySnapshot, ...]:
    engine = SignalEligibilityAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of ScoreSnapshot records") from exc
    return tuple(engine.update(frame) for frame in iterator)
