"""First-violation formation bookkeeping over published OBs, never a live zone manager."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.breaker_blocks.calculation import validate_frames, violates
from smcsignal.analysis.breaker_blocks.config import BreakerBlockConfig
from smcsignal.analysis.breaker_blocks.evidence import (
    build_block,
    build_evidence,
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.breaker_blocks.models import BreakerEvidence, BreakerSnapshot
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.mss.calculation import source_frame
from smcsignal.analysis.mss.models import MSSSnapshot
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.provenance import SeriesProvenance


class BreakerBlockAnalyzer:
    """Evaluate every eligible original OB once at its first strict closing violation."""

    def __init__(self, config: BreakerBlockConfig | None = None) -> None:
        if config is not None and not isinstance(config, BreakerBlockConfig):
            raise AnalysisConfigurationError("config must be BreakerBlockConfig")
        self._config = config if config is not None else BreakerBlockConfig()
        self._candidates: dict[str, OrderBlockEvent] = {}
        self._seen: set[str] = set()
        self._latest: BreakerSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._liquidity_hash: str | None = None

    @property
    def config(self) -> BreakerBlockConfig:
        return self._config

    @property
    def latest(self) -> BreakerSnapshot | None:
        return self._latest

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def series(self) -> SeriesProvenance | None:
        return self._latest.provenance.series if self._latest is not None else None

    @property
    def price_unit(self) -> str | None:
        return (
            source_frame(self._latest.upstream.upstream).price_unit
            if self._latest is not None
            else None
        )

    @property
    def configuration_artifact(self) -> bytes | None:
        return self._artifact

    def update(self, frame: MSSSnapshot) -> BreakerSnapshot:
        """Consume one completed frame; commit only after every assessment succeeds."""
        if (
            not isinstance(frame, MSSSnapshot)
            or frame.upstream.observation.reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "Breaker requires original MSS frames from zero in consecutive unique order"
            )
        previous = self._latest.upstream if self._latest is not None else None
        validate_frames(previous, frame)
        source = source_frame(frame.upstream)
        liquidity_hash = self._liquidity_hash
        for pool in (*source.liquidity.pool_updates, *(s.pool for s in source.liquidity.sweeps)):
            if pool.settings.price_unit != source.price_unit:
                raise AnalysisInputError("upstream price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError(
                    "upstream liquidity configuration cannot change during Breaker replay"
                )
            liquidity_hash = pool.provenance.configuration_hash
        artifact = (
            self._artifact
            if self._artifact is not None
            else configuration_artifact(self.config, source.price_unit)
        )
        config_hash = sha256(artifact).hexdigest()
        candidates, seen = self._candidates.copy(), self._seen.copy()
        evidence: list[BreakerEvidence] = []
        current = frame.upstream.observation
        # All independent prior OBs, in original publication order; no nearest-only loss.
        for key, origin in self._candidates.items():
            if violates(origin, current.candle.close):
                evidence.append(build_evidence(origin, previous, frame, self.config, config_hash))
                # One first violation, including rejection for missing confirmation.
                del candidates[key]
        # Newly available OBs cannot be treated as known before their own publication bar.
        for origin in frame.upstream.upstream.events:
            if origin.event_id in seen:
                raise AnalysisInputError("duplicate/conflicting original OB publication")
            seen.add(origin.event_id)
            if violates(origin, current.candle.close):
                evidence.append(build_evidence(origin, previous, frame, self.config, config_hash))
            else:
                candidates[origin.event_id] = origin
        published = tuple(evidence)
        events = tuple(build_block(item) for item in published if item.qualified)
        result = BreakerSnapshot(
            self.config,
            frame,
            previous,
            published,
            events,
            snapshot_provenance(previous, frame, published, events, config_hash),
        )
        # A failed update changes no formation bookkeeping or historical objects.
        self._candidates, self._seen = candidates, seen
        self._latest, self._artifact, self._liquidity_hash = result, artifact, liquidity_hash
        self._count += 1
        return result


def analyze_breaker_blocks(
    frames: Iterable[MSSSnapshot], config: BreakerBlockConfig | None = None
) -> tuple[BreakerSnapshot, ...]:
    """Replay existing MSS frames using the identical incremental formation loop."""
    analyzer = BreakerBlockAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of MSSSnapshot records") from exc
    return tuple(analyzer.update(frame) for frame in iterator)
