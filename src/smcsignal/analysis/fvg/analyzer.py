"""Deterministic creation-only FVG detection over the existing Phase 5 stream."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.fvg.calculation import qualified_gap, validate_window
from smcsignal.analysis.fvg.config import FVGConfig
from smcsignal.analysis.fvg.evidence import configuration_artifact, fvg_provenance
from smcsignal.analysis.fvg.models import FVGEvent, FVGSnapshot
from smcsignal.analysis.provenance import SeriesProvenance


class FVGAnalyzer:
    """Keep only a three-frame window; never rerun upstream engines or update old gaps."""

    def __init__(self, config: FVGConfig | None = None) -> None:
        if config is not None and not isinstance(config, FVGConfig):
            raise AnalysisConfigurationError("config must be FVGConfig")
        self._config = config if config is not None else FVGConfig()
        self._window: tuple[DisplacementSnapshot, ...] = ()
        self._latest: FVGSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._liquidity_hash: str | None = None

    @property
    def config(self) -> FVGConfig:
        return self._config

    @property
    def latest(self) -> FVGSnapshot | None:
        return self._latest

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def series(self) -> SeriesProvenance | None:
        return self._latest.provenance.series if self._latest is not None else None

    @property
    def price_unit(self) -> str | None:
        return self._window[-1].price_unit if self._window else None

    @property
    def configuration_artifact(self) -> bytes | None:
        """Units bind on the first successful input, never by guessing the quote asset."""
        return self._artifact

    def update(self, frame: DisplacementSnapshot) -> FVGSnapshot:
        if not isinstance(frame, DisplacementSnapshot):
            raise AnalysisInputError("FVG requires an existing DisplacementSnapshot, not raw data")
        if frame.metrics.observation.reference.candle_index != self._count:
            raise AnalysisInputError(
                "FVG inputs must start at zero and be consecutive, unique observations"
            )
        window = (*self._window, frame)[-3:]
        validate_window(window)
        # Bind all visible upstream liquidity configuration versions without rerunning it.
        liquidity_hash = self._liquidity_hash
        for pool in (*frame.liquidity.pool_updates, *(s.pool for s in frame.liquidity.sweeps)):
            if pool.settings.price_unit != frame.price_unit:
                raise AnalysisInputError("upstream price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError("upstream liquidity configuration cannot change midstream")
            liquidity_hash = pool.provenance.configuration_hash
        artifact = (
            self._artifact
            if self._artifact is not None
            else configuration_artifact(self.config, frame.price_unit)
        )
        config_hash = sha256(artifact).hexdigest()
        events: tuple[FVGEvent, ...] = ()
        if qualified_gap(window, self.config) is not None:
            events = (
                FVGEvent(
                    self.config, window, fvg_provenance(window, config_hash, producer="fvg-event")
                ),
            )
        result = FVGSnapshot(
            self.config,
            window,
            events,
            fvg_provenance(
                window,
                config_hash,
                producer="fvg-frame",
                extra=tuple(e.provenance.as_reference() for e in events),
            ),
        )
        # All validation, arithmetic and evidence construction must succeed before mutation.
        self._window, self._latest, self._artifact = window, result, artifact
        self._liquidity_hash = liquidity_hash
        self._count += 1
        return result


def analyze_fvg(
    frames: Iterable[DisplacementSnapshot], config: FVGConfig | None = None
) -> tuple[FVGSnapshot, ...]:
    """Batch is the identical update loop over existing frames; no look-ahead pass."""
    analyzer = FVGAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError(
            "frames must be an iterable of DisplacementSnapshot records"
        ) from exc
    return tuple(analyzer.update(frame) for frame in iterator)
