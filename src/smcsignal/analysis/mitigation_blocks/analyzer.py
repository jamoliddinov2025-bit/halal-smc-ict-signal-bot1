"""First-interaction bookkeeping over published OBs, never a live zone manager."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.breaker_blocks.models import BreakerSnapshot
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.mitigation_blocks.calculation import (
    converted_ids,
    intersects,
    known_at_open,
    observation,
    published_order_blocks,
    validate_frames,
)
from smcsignal.analysis.mitigation_blocks.config import MitigationBlockConfig
from smcsignal.analysis.mitigation_blocks.evidence import (
    build_block,
    build_evidence,
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.mitigation_blocks.models import MitigationEvidence, MitigationSnapshot
from smcsignal.analysis.mss.calculation import source_frame
from smcsignal.analysis.order_blocks.models import OrderBlockEvent
from smcsignal.analysis.provenance import SeriesProvenance


class MitigationBlockAnalyzer:
    """Evaluate every eligible original OB once at its first interior overlap."""

    def __init__(self, config: MitigationBlockConfig | None = None) -> None:
        if config is not None and not isinstance(config, MitigationBlockConfig):
            raise AnalysisConfigurationError("config must be MitigationBlockConfig")
        self._config = config if config is not None else MitigationBlockConfig()
        self._candidates: dict[str, OrderBlockEvent] = {}
        self._seen: set[str] = set()
        self._broken: set[str] = set()
        self._latest: MitigationSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._liquidity_hash: str | None = None

    @property
    def config(self) -> MitigationBlockConfig:
        return self._config

    @property
    def latest(self) -> MitigationSnapshot | None:
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
            source_frame(self._latest.upstream.upstream.upstream).price_unit
            if self._latest is not None
            else None
        )

    @property
    def configuration_artifact(self) -> bytes | None:
        return self._artifact

    def update(self, frame: BreakerSnapshot) -> MitigationSnapshot:
        """Consume one completed frame; commit only after every assessment succeeds."""
        if (
            not isinstance(frame, BreakerSnapshot)
            or observation(frame).reference.candle_index != self._count
        ):
            raise AnalysisInputError(
                "Mitigation requires original Breaker frames from zero in consecutive unique order"
            )
        previous = self._latest.upstream if self._latest is not None else None
        validate_frames(previous, frame)
        source = source_frame(frame.upstream.upstream)
        liquidity_hash = self._liquidity_hash
        for pool in (*source.liquidity.pool_updates, *(s.pool for s in source.liquidity.sweeps)):
            if pool.settings.price_unit != source.price_unit:
                raise AnalysisInputError("upstream price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError(
                    "upstream liquidity configuration cannot change during Mitigation replay"
                )
            liquidity_hash = pool.provenance.configuration_hash
        artifact = (
            self._artifact
            if self._artifact is not None
            else configuration_artifact(self.config, source.price_unit)
        )
        config_hash = sha256(artifact).hexdigest()
        candidates, seen, broken = self._candidates.copy(), self._seen.copy(), self._broken.copy()
        current = observation(frame)
        if self.config.ignore_after_breaker:
            for ob_id in converted_ids(frame):
                broken.add(ob_id)
                candidates.pop(ob_id, None)
        evidence: list[MitigationEvidence] = []
        # Independent prior OBs, in original publication order; no nearest-only loss.
        remaining: dict[str, OrderBlockEvent] = {}
        for key, origin in candidates.items():
            if key in broken:
                continue
            if known_at_open(origin, current) and intersects(origin, current.candle):
                evidence.append(build_evidence(origin, previous, frame, self.config, config_hash))
                if not self.config.first_interaction_only:
                    remaining[key] = origin
            else:
                remaining[key] = origin
        # Newly available OBs cannot mitigate on their own publication bar.
        for origin in published_order_blocks(frame):
            if origin.event_id in seen:
                raise AnalysisInputError("duplicate/conflicting original OB publication")
            seen.add(origin.event_id)
            if origin.event_id not in broken:
                remaining[origin.event_id] = origin
        published = tuple(evidence)
        events = tuple(build_block(item) for item in published)
        result = MitigationSnapshot(
            self.config,
            frame,
            previous,
            published,
            events,
            snapshot_provenance(previous, frame, published, events, config_hash),
        )
        self._candidates, self._seen, self._broken = remaining, seen, broken
        self._latest, self._artifact, self._liquidity_hash = result, artifact, liquidity_hash
        self._count += 1
        return result


def analyze_mitigation_blocks(
    frames: Iterable[BreakerSnapshot], config: MitigationBlockConfig | None = None
) -> tuple[MitigationSnapshot, ...]:
    """Replay existing Breaker frames using the identical incremental interaction loop."""
    analyzer = MitigationBlockAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of BreakerSnapshot records") from exc
    return tuple(analyzer.update(frame) for frame in iterator)
