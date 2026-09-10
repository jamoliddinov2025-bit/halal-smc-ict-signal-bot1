"""Frame-native causal OB formation; at most one next-candle FVG wait, no lifecycle."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.fvg.calculation import validate_window
from smcsignal.analysis.fvg.models import FVGSnapshot
from smcsignal.analysis.order_blocks.calculation import (
    formation_candidate,
    matching_fvg,
    validate_history,
)
from smcsignal.analysis.order_blocks.config import OrderBlockConfig
from smcsignal.analysis.order_blocks.evidence import (
    build_event,
    configuration_artifact,
    snapshot_provenance,
)
from smcsignal.analysis.order_blocks.models import OrderBlockEvent, OrderBlockSnapshot
from smcsignal.analysis.provenance import SeriesProvenance


@dataclass(frozen=True, slots=True)
class _Pending:
    history: tuple[DisplacementSnapshot, ...]
    displacement_frame: DisplacementSnapshot


class OrderBlockAnalyzer:
    """One series of formation evidence; no upstream recalculation or zone lifecycle."""

    def __init__(self, config: OrderBlockConfig | None = None) -> None:
        if config is not None and not isinstance(config, OrderBlockConfig):
            raise AnalysisConfigurationError("config must be OrderBlockConfig")
        self._config = config if config is not None else OrderBlockConfig()
        self._history: tuple[DisplacementSnapshot, ...] = ()
        self._pending: _Pending | None = None
        self._latest: OrderBlockSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None
        self._liquidity_hash: str | None = None

    @property
    def config(self) -> OrderBlockConfig:
        return self._config

    @property
    def latest(self) -> OrderBlockSnapshot | None:
        return self._latest

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def series(self) -> SeriesProvenance | None:
        return self._latest.provenance.series if self._latest is not None else None

    @property
    def price_unit(self) -> str | None:
        return self._history[-1].price_unit if self._history else None

    @property
    def configuration_artifact(self) -> bytes | None:
        return self._artifact

    def _validate(self, frame: FVGSnapshot) -> str | None:
        if not isinstance(frame, FVGSnapshot):
            raise AnalysisInputError(
                "Order Blocks consume existing FVGSnapshot frames, not raw data"
            )
        current = frame.upstream
        if current.metrics.observation.reference.candle_index != self._count:
            raise AnalysisInputError(
                "OB input must start at zero and be consecutive unique observed indices"
            )
        if self._latest is not None:
            previous = self._latest.upstream
            validate_window((previous.upstream, current))
            if (
                previous.provenance.configuration_hash != frame.provenance.configuration_hash
                or previous.settings != frame.settings
            ):
                raise AnalysisInputError("upstream FVG configuration cannot change during replay")
        liquidity_hash = self._liquidity_hash
        for pool in (*current.liquidity.pool_updates, *(s.pool for s in current.liquidity.sweeps)):
            if pool.settings.price_unit != current.price_unit:
                raise AnalysisInputError("upstream price units differ")
            if liquidity_hash is not None and pool.provenance.configuration_hash != liquidity_hash:
                raise AnalysisInputError(
                    "upstream liquidity configuration cannot change during replay"
                )
            liquidity_hash = pool.provenance.configuration_hash
        return liquidity_hash

    def update(self, frame: FVGSnapshot) -> OrderBlockSnapshot:
        """Consume one closed frame and resolve only the prior next-candle FVG wait."""
        liquidity_hash = self._validate(frame)
        current = frame.upstream
        validate_history(self._history, current, self.config)
        artifact = (
            self._artifact
            if self._artifact is not None
            else configuration_artifact(self.config, current.price_unit)
        )
        config_hash = sha256(artifact).hexdigest()
        events: tuple[OrderBlockEvent, ...] = ()
        next_pending = None
        if self.config.require_fvg and self._pending is not None:
            pending = self._pending
            if matching_fvg(pending.displacement_frame, frame) is not None:
                events = (
                    build_event(
                        pending.history, pending.displacement_frame, frame, self.config, config_hash
                    ),
                )
        # Freeze the displacement-time history; never reselect from C3 history.
        match = formation_candidate(self._history, current, self.config)
        if match is not None:
            if self.config.require_fvg:
                next_pending = _Pending(self._history, current)
            else:
                events = (build_event(self._history, current, frame, self.config, config_hash),)
        result = OrderBlockSnapshot(
            self.config, frame, events, snapshot_provenance(frame, events, config_hash)
        )
        # Commit state only after successful validation, evidence creation and model checks.
        self._history = (*self._history, current)[-self.config.max_candidate_lookback :]
        self._pending, self._latest, self._artifact = next_pending, result, artifact
        self._liquidity_hash = liquidity_hash
        self._count += 1
        return result


def analyze_order_blocks(
    frames: Iterable[FVGSnapshot], config: OrderBlockConfig | None = None
) -> tuple[OrderBlockSnapshot, ...]:
    analyzer = OrderBlockAnalyzer(config)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of FVGSnapshot records") from exc
    return tuple(analyzer.update(frame) for frame in iterator)
