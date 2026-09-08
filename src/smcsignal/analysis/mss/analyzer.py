"""Strict causal MSS events over existing Phase 8 frames; no state relabeling."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.mss.calculation import qualifying_shift, source_frame, validate_pair
from smcsignal.analysis.mss.config import MSSConfig
from smcsignal.analysis.mss.evidence import (
    build_event,
    build_evidence,
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.mss.models import MSSEvent, MSSEvidence, MSSSnapshot
from smcsignal.analysis.premium_discount.models import PDSnapshot
from smcsignal.analysis.provenance import SeriesProvenance


class MSSAnalyzer:
    """One immutable assessment per PD frame; no upstream detectors or regime overrides."""

    def __init__(self, config: MSSConfig | None = None) -> None:
        if config is not None and not isinstance(config, MSSConfig):
            raise AnalysisConfigurationError("config must be MSSConfig")
        self._config = config if config is not None else MSSConfig()
        self._latest: MSSSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._liquidity_hash: str | None = None

    @property
    def config(self) -> MSSConfig:
        return self._config

    @property
    def latest(self) -> MSSSnapshot | None:
        return self._latest

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def series(self) -> SeriesProvenance | None:
        return self._latest.provenance.series if self._latest is not None else None

    @property
    def price_unit(self) -> str | None:
        return source_frame(self._latest.upstream).price_unit if self._latest is not None else None

    @property
    def configuration_artifact(self) -> bytes | None:
        return self._artifact

    def update(self, frame: PDSnapshot) -> MSSSnapshot:
        if (
            not isinstance(frame, PDSnapshot)
            or frame.observation.reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "MSS requires existing PD frames starting at zero in consecutive unique order"
            )
        previous = self._latest.upstream if self._latest is not None else None
        validate_pair(previous, frame)
        source = source_frame(frame)
        liquidity_hash = self._liquidity_hash
        for pool in (*source.liquidity.pool_updates, *(s.pool for s in source.liquidity.sweeps)):
            if pool.settings.price_unit != source.price_unit:
                raise AnalysisInputError("MSS upstream price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError("MSS upstream liquidity configuration cannot change")
            liquidity_hash = pool.provenance.configuration_hash
        artifact = (
            self._artifact
            if self._artifact is not None
            else configuration_artifact(self.config, source.price_unit)
        )
        config_hash = sha256(artifact).hexdigest()
        evidence: tuple[MSSEvidence, ...] = ()
        events: tuple[MSSEvent, ...] = ()
        if qualifying_shift(previous, frame) is not None:
            assert previous is not None
            item = build_evidence(previous, frame, self.config, config_hash)
            evidence, events = (item,), (build_event(item),)
        result = MSSSnapshot(
            self.config,
            frame,
            previous,
            evidence,
            events,
            snapshot_provenance(previous, frame, evidence, events, config_hash),
        )
        # Commit only after every causal/model/identity check completes successfully.
        self._latest, self._artifact, self._liquidity_hash = result, artifact, liquidity_hash
        self._count += 1
        return result


def analyze_mss(
    frames: Iterable[PDSnapshot], config: MSSConfig | None = None
) -> tuple[MSSSnapshot, ...]:
    analyzer = MSSAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of PDSnapshot records") from exc
    return tuple(analyzer.update(frame) for frame in iterator)
