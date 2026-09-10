"""Track BUY_SIGNAL outcomes over existing Phase 17 frames without lookahead."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.outcome_tracking.calculation import (
    advance_outcome,
    aggregate,
    open_outcome,
)
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.outcome_tracking.evidence import (
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.outcome_tracking.models import (
    OutcomeSnapshot,
    OutcomeStatus,
    SignalOutcome,
    current_observation,
)
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.signal_engine.config import SignalEngineConfig
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus


class OutcomeTrackingAnalyzer:
    """Consumer-only outcome analytics over published spot signal frames.

    BUY_SIGNAL frames open exactly one outcome; every later closed candle
    evaluates all open outcomes against the fixed horizon. Open outcomes stay
    open when a replay ends: no end-of-series flush exists. State commits only
    after every check, version, and provenance construction succeeds.
    """

    def __init__(self, config: OutcomeTrackingConfig | None = None) -> None:
        if config is not None and not isinstance(config, OutcomeTrackingConfig):
            raise AnalysisConfigurationError("config must be OutcomeTrackingConfig")
        self._config = config if config is not None else OutcomeTrackingConfig()
        self._latest: OutcomeSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._records: dict[str, SignalOutcome] = {}
        self._tracked: set[str] = set()
        self._signal_settings: SignalEngineConfig | None = None

    @property
    def config(self) -> OutcomeTrackingConfig:
        return self._config

    @property
    def latest(self) -> OutcomeSnapshot | None:
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
    def open_outcomes(self) -> tuple[SignalOutcome, ...]:
        """Read-only creation-ordered view of currently open outcome versions."""

        return tuple(
            record for record in self._records.values() if record.status is OutcomeStatus.OPEN
        )

    @property
    def finalized_outcomes(self) -> tuple[SignalOutcome, ...]:
        """Read-only creation-ordered view of finalized outcome versions."""

        return tuple(
            record for record in self._records.values() if record.status is not OutcomeStatus.OPEN
        )

    def update(self, frame: SignalSnapshot) -> OutcomeSnapshot:
        """Consume one signal frame; evaluate open outcomes, then open new ones."""

        if not isinstance(frame, SignalSnapshot):
            raise AnalysisInputError(
                "outcome tracking requires existing SignalSnapshot frames starting at zero "
                "in consecutive unique order"
            )
        new_signal = frame.status is SignalStatus.BUY_SIGNAL
        if new_signal and frame.signal_id in self._tracked:
            raise AnalysisInputError("each BUY_SIGNAL is tracked by at most one outcome")
        if current_observation(frame).reference.candle_index != self._count:
            raise AnalysisInputError(
                "outcome tracking requires existing SignalSnapshot frames starting at zero "
                "in consecutive unique order"
            )
        observation = current_observation(frame)
        series = frame.provenance.series
        if self._latest is not None:
            prior = current_observation(self._latest.upstream)
            if series != self._latest.upstream.provenance.series:
                raise AnalysisInputError("outcome tracking series cannot change during a replay")
            if frame.provenance.available_at < self._latest.upstream.provenance.available_at:
                raise AnalysisInputError("outcome tracking availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError(
                    "outcome tracking candles must be chronological and unique"
                )
        if self._signal_settings is not None and frame.settings != self._signal_settings:
            raise AnalysisInputError("outcome tracking requires one signal engine configuration")
        artifact = (
            self._artifact if self._artifact is not None else configuration_artifact(self.config)
        )
        config_hash = sha256(artifact).hexdigest()
        evaluated = tuple(
            advance_outcome(record, frame, self.config, config_hash)
            for record in self._records.values()
            if record.status is OutcomeStatus.OPEN
        )
        completed = tuple(record for record in evaluated if record.status is not OutcomeStatus.OPEN)
        created: tuple[SignalOutcome, ...] = ()
        if new_signal:
            created = (open_outcome(frame, self.config, config_hash),)
        latest_versions: dict[str, SignalOutcome] = dict(self._records)
        for record in (*evaluated, *created):
            latest_versions[record.outcome_id] = record
        finalized = tuple(
            record for record in latest_versions.values() if record.status is not OutcomeStatus.OPEN
        )
        open_count = sum(record.status is OutcomeStatus.OPEN for record in latest_versions.values())
        analytics = aggregate(
            finalized, total_buy_signals=len(latest_versions), open_count=open_count
        )
        records = (*created, *evaluated)
        result = OutcomeSnapshot(
            self.config,
            frame,
            created,
            evaluated,
            completed,
            analytics,
            snapshot_provenance(frame, records, analytics, config_hash),
        )
        for record in records:
            self._records[record.outcome_id] = record
        if new_signal:
            self._tracked.add(frame.signal_id)
        self._signal_settings = frame.settings
        self._latest, self._artifact = result, artifact
        self._count += 1
        return result


def analyze_outcome_tracking(
    frames: Iterable[SignalSnapshot], config: OutcomeTrackingConfig | None = None
) -> tuple[OutcomeSnapshot, ...]:
    """Sequential batch replay; identical to streaming and chunked updates."""

    tracker = OutcomeTrackingAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of SignalSnapshot records") from exc
    return tuple(tracker.update(frame) for frame in iterator)
