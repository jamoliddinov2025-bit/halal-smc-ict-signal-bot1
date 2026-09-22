"""Phase 35E: explicit bounded retention ownership for live runtime state.

Classification of live state (the seven categories the checkpoint and the
service must distinguish):

1. **Minimum raw candle context** — the finite local lookback the Phase 3-17
   analyzers need to interpret the most recent candles (gap continuity,
   evidence references). Derived, never magic: the conservative local lower
   bound is ``max(fractal_length, atr_period + 1, sweep_lookback_bars,
   max_candidate_lookback, 3)`` over the declared ``BacktestConfiguration``.
   Service windows that only serve gap detection and local context are
   bounded by this value.

2. **Analyzer state required to continue deterministically** — frozen
   analyzer internals (rolling prefix hasher, structure levels, active pools,
   pending candidates, counters). No safe finite eviction horizon exists for
   several of these components, so they are retained through warm-up replay
   and the explicit checkpoint — never silently truncated.

3. **HTF/MTF context** — higher-timeframe candles and their materialized
   OTE frames plus the MTF cursors/published evidence. Retained (no safe
   finite eviction horizon): a later HTF update must rebuild downstream
   state exactly. Materialization is append-cached so the cost of an HTF
   change does not reprocess the entire higher history.

4. **Duplicate-fencing state** — the signal engine's published setup
   identities. Never evicted; always serialized in the checkpoint.

5. **Provenance/evidence state** — latest snapshots and evidence references
   required by downstream publication. Retained via the checkpoint.

6. **Checkpoint state** — the explicit versioned durable representation
   owned by :mod:`smcsignal.live.checkpoint`.

7. **Recovery state** — full primary/HTF candle histories required for the
   frozen Phase 26G verified ledger open (warm-up replay from index zero).
   Held in the runtime's retained OTE/HTF context and mirrored into the
   checkpoint; service windows may be bounded because recovery reads the
   checkpoint first.

Eviction is deterministic, configuration-driven, and applied only at the
service ownership boundary after the checkpoint that preserves the full
recovery state has been persisted. Duplicate fencing, required evidence,
required MTF state, and required analyzer state are never evicted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.data.models import OHLCV

#: FVG's required three-frame window (creation needs exactly three candles).
FVG_FRAME_WINDOW = 3


def local_context_bound(configuration: BacktestConfiguration) -> int:
    """Conservative local raw-candle lower bound derived from configuration.

    ``max(fractal_length, atr_period + 1, sweep_lookback_bars,
    max_candidate_lookback, 3)`` — every term is a declared Phase 3-19
    configuration requirement, not an arbitrary retention constant.
    """

    if not isinstance(configuration, BacktestConfiguration):
        raise AnalysisInputError("local_context_bound requires a BacktestConfiguration")
    analysis = configuration.analysis if configuration.analysis is not None else AnalysisConfig()
    return max(
        analysis.fractal_length,
        configuration.displacement.atr_period + 1,
        configuration.displacement.sweep_lookback_bars,
        configuration.order_blocks.max_candidate_lookback,
        FVG_FRAME_WINDOW,
    )


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Validated, configuration-derived retention bounds for live state."""

    local_context_bound: int

    def __post_init__(self) -> None:
        if type(self.local_context_bound) is not int or self.local_context_bound < FVG_FRAME_WINDOW:
            raise AnalysisInputError(
                "local_context_bound must be an integer >= 3 derived from configuration"
            )

    @classmethod
    def from_configuration(cls, configuration: BacktestConfiguration) -> RetentionPolicy:
        """Build the policy implied by one declared pipeline configuration."""

        return cls(local_context_bound=local_context_bound(configuration))

    def bound_candles(self, candles: Sequence[OHLCV]) -> tuple[OHLCV, ...]:
        """Deterministically retain only the newest ``local_context_bound`` candles.

        Returns the input unchanged when already within the bound; never
        mutates the input. This is the only sanctioned raw-context eviction
        for service windows — recovery state lives in the checkpoint.
        """

        if not isinstance(candles, Sequence):
            raise AnalysisInputError("bound_candles requires a sequence of OHLCV candles")
        retained = tuple(candles)
        if len(retained) <= self.local_context_bound:
            return retained
        return retained[-self.local_context_bound :]


__all__ = [
    "FVG_FRAME_WINDOW",
    "RetentionPolicy",
    "local_context_bound",
]
