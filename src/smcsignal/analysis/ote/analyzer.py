"""Classify completed closes against already-known Phase 8 OTE geometry."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.ote.calculation import known_at_open, source_frame, validate_frames
from smcsignal.analysis.ote.config import OTEConfig
from smcsignal.analysis.ote.evidence import (
    build_observation,
    build_zone,
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.ote.models import OTESnapshot, OTEZone
from smcsignal.analysis.premium_discount.models import PDSnapshot
from smcsignal.analysis.provenance import SeriesProvenance


class OTEAnalyzer:
    """Each output classifies the current close using only a previously known range."""

    def __init__(self, config: OTEConfig | None = None) -> None:
        if config is not None and not isinstance(config, OTEConfig):
            raise AnalysisConfigurationError("config must be OTEConfig")
        self._config = config if config is not None else OTEConfig()
        self._latest: OTESnapshot | None = None
        self._zone: OTEZone | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._liquidity_hash: str | None = None

    @property
    def config(self) -> OTEConfig:
        return self._config

    @property
    def latest(self) -> OTESnapshot | None:
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

    def update(self, frame: PDSnapshot) -> OTESnapshot:
        if (
            not isinstance(frame, PDSnapshot)
            or frame.observation.reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "OTE requires existing PD frames starting at zero in consecutive unique order"
            )
        previous = self._latest.upstream if self._latest is not None else None
        validate_frames(previous, frame)
        source = source_frame(frame)
        liquidity_hash = self._liquidity_hash
        for pool in (*source.liquidity.pool_updates, *(s.pool for s in source.liquidity.sweeps)):
            if pool.settings.price_unit != source.price_unit:
                raise AnalysisInputError("OTE upstream price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError("OTE upstream liquidity configuration cannot change")
            liquidity_hash = pool.provenance.configuration_hash
        artifact = (
            self._artifact
            if self._artifact is not None
            else configuration_artifact(self.config, source.price_unit)
        )
        config_hash = sha256(artifact).hexdigest()
        dealing_range = frame.dealing_range
        if dealing_range is None:
            zone = None
        elif self._zone is not None and self._zone.dealing_range is dealing_range:
            zone = self._zone
        else:
            zone = build_zone(dealing_range, self.config, config_hash)
        current = frame.observation
        usable = zone if zone is not None and known_at_open(zone.dealing_range, current) else None
        item = build_observation(
            self.config, usable, current, config_hash, frame.provenance.input_prefix_hash
        )
        result = OTESnapshot(
            self.config,
            frame,
            zone,
            item,
            snapshot_provenance(frame, zone, item, config_hash),
        )
        self._zone, self._latest, self._artifact, self._liquidity_hash = (
            zone,
            result,
            artifact,
            liquidity_hash,
        )
        self._count += 1
        return result


def analyze_ote(
    frames: Iterable[PDSnapshot], config: OTEConfig | None = None
) -> tuple[OTESnapshot, ...]:
    engine = OTEAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of PDSnapshot records") from exc
    return tuple(engine.update(frame) for frame in iterator)
