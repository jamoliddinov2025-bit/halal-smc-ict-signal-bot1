"""Local-series PD evaluation over existing Phase 7 frames, with immutable sidecars."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.fvg.calculation import validate_window
from smcsignal.analysis.liquidity.models import SwingEvidence
from smcsignal.analysis.models import SwingKind
from smcsignal.analysis.order_blocks.models import OrderBlockSnapshot
from smcsignal.analysis.premium_discount.calculation import (
    RangeStatus,
    pair_status,
    published_arrays,
)
from smcsignal.analysis.premium_discount.config import PDConfig
from smcsignal.analysis.premium_discount.evidence import (
    build_array_context,
    build_equilibrium,
    build_range,
    configuration_artifacts,
    snapshot_provenance,
)
from smcsignal.analysis.premium_discount.models import DealingRange, Equilibrium, PDSnapshot
from smcsignal.analysis.provenance import SeriesProvenance


class PDAnalyzer:
    """Each output evaluates the close and new arrays using only known confirmed swings."""

    def __init__(self, config: PDConfig | None = None) -> None:
        if config is not None and not isinstance(config, PDConfig):
            raise AnalysisConfigurationError("config must be PDConfig")
        self._config = config if config is not None else PDConfig()
        self._latest: PDSnapshot | None = None
        self._low: SwingEvidence | None = None
        self._high: SwingEvidence | None = None
        self._range: DealingRange | None = None
        self._equilibrium: Equilibrium | None = None
        self._artifacts: tuple[bytes, bytes] | None = None
        self._liquidity_hash: str | None = None
        self._count = 0

    @property
    def config(self) -> PDConfig:
        return self._config

    @property
    def latest(self) -> PDSnapshot | None:
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
            self._latest.upstream.upstream.upstream.price_unit if self._latest is not None else None
        )

    @property
    def configuration_artifact(self) -> bytes | None:
        return self._artifacts[0] if self._artifacts is not None else None

    @property
    def range_configuration_artifact(self) -> bytes | None:
        return self._artifacts[1] if self._artifacts is not None else None

    def _validate(self, frame: OrderBlockSnapshot) -> str | None:
        if not isinstance(frame, OrderBlockSnapshot):
            raise AnalysisInputError("PD consumes existing OrderBlockSnapshot frames, not raw data")
        source = frame.upstream.upstream
        if source.metrics.observation.reference.candle_index != self._count:
            raise AnalysisInputError(
                "PD input must start at zero with consecutive unique observed indices"
            )
        if self._latest is not None:
            previous = self._latest.upstream
            validate_window((previous.upstream.upstream, source))
            if (
                previous.provenance.configuration_hash != frame.provenance.configuration_hash
                or previous.settings != frame.settings
            ):
                raise AnalysisInputError(
                    "upstream Order Block configuration cannot change during PD replay"
                )
            if (
                previous.upstream.provenance.configuration_hash
                != frame.upstream.provenance.configuration_hash
            ):
                raise AnalysisInputError(
                    "upstream FVG configuration cannot change during PD replay"
                )
        liquidity_hash = self._liquidity_hash
        for pool in (*source.liquidity.pool_updates, *(s.pool for s in source.liquidity.sweeps)):
            if pool.settings.price_unit != source.price_unit:
                raise AnalysisInputError("upstream price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError(
                    "upstream liquidity configuration cannot change during PD replay"
                )
            liquidity_hash = pool.provenance.configuration_hash
        return liquidity_hash

    def update(self, frame: OrderBlockSnapshot) -> PDSnapshot:
        liquidity_hash = self._validate(frame)
        source = frame.upstream.upstream
        observation, context = source.metrics.observation, source.liquidity.context
        artifacts = (
            self._artifacts
            if self._artifacts is not None
            else configuration_artifacts(self.config, source.price_unit)
        )
        config_hash, range_hash = (sha256(value).hexdigest() for value in artifacts)
        low, high = self._low, self._high
        # Consume only already-published confirmations; do not detect/alter swings.
        for swing in source.liquidity.confirmed_swings:
            if swing.swing.kind == SwingKind.LOW:
                low = swing
            else:
                high = swing
        dealing_range, equilibrium = self._range, self._equilibrium
        if pair_status(low, high) != RangeStatus.CONFIRMED:
            dealing_range = equilibrium = None
        elif (
            dealing_range is None
            or dealing_range.low_swing != low
            or dealing_range.high_swing != high
        ):
            assert low is not None and high is not None
            dealing_range = build_range(low, high, context, source.price_unit, range_hash)
            equilibrium = build_equilibrium(dealing_range, self.config, config_hash)
        arrays = tuple(
            build_array_context(subject, observation, dealing_range, equilibrium, config_hash)
            for subject in published_arrays(frame)
        )
        result = PDSnapshot(
            self.config,
            frame,
            low,
            high,
            dealing_range,
            equilibrium,
            arrays,
            snapshot_provenance(frame, low, high, dealing_range, equilibrium, arrays, config_hash),
        )
        # Transactional local update: a rejected frame cannot mutate old ranges/sidecars/state.
        self._low, self._high, self._range, self._equilibrium = (
            low,
            high,
            dealing_range,
            equilibrium,
        )
        self._latest, self._artifacts, self._liquidity_hash = result, artifacts, liquidity_hash
        self._count += 1
        return result


def analyze_pd(
    frames: Iterable[OrderBlockSnapshot], config: PDConfig | None = None
) -> tuple[PDSnapshot, ...]:
    engine = PDAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError(
            "frames must be an iterable of OrderBlockSnapshot records"
        ) from exc
    return tuple(engine.update(frame) for frame in iterator)
