"""Phase 26F: deterministic series frame source — the sanctioned regeneration seam.

Phase 26E recovery requires the series' historical ``SignalSnapshot`` sequence
"regenerated through the real Phase 3-17 chain" — but until Phase 26F that
regeneration existed only in test glue. This module is the production seam:
given one declared historical ``ReplayDataset`` and one ``BacktestConfiguration``,
it emits the exact real Phase 17 publication frames the frozen pipeline
produces, chronologically, one declared candle at a time.

Real publication frames only (LOCK 1)
--------------------------------------

Every frame returned is the actual ``SignalSnapshot`` the frozen Phase 17
signal engine published inside the frozen Phase 20 ``HistoricalReplay`` —
consumed from ``ReplayStep.signal``, never manufactured, reconstructed, or
re-published here. No ``SignalSnapshot`` value is built by this module.

Reuse, not duplication (LOCK 2)
-------------------------------

The source wraps one ``HistoricalReplay`` and owns no analyzer composition of
its own: liquidity, displacement, FVG, order blocks, premium/discount, OTE,
MTF, halal filtering, setup quality, eligibility, and the signal engine stay
exactly where the frozen Phase 20 replay composes them. The draw cursor,
chronological order, and higher-timeframe availability gating are the
replay's; this module introduces no second cursor and no parallel state
machine. The replay also performs its deterministic Phase 18/19 outcome and
attribution work per step: that is inherited frozen replay behavior — it is
not exposed here, connects to nothing downstream, and no second outcome
system exists in Phase 26F.

Frames in the caller's service (LOCK 3)
---------------------------------------

The source knows no ledger: no outcome records, no ledger snapshots, no
persistence keys, no recovery, no delivery state. It emits frames; feeding a
Phase 26B lifecycle, persisting Phase 26C bytes, or recovering through Phase
26E remains the caller's composition. Strictly offline and value-driven: no
clock, file, network, websocket, scheduler, sleep, or concurrency — candle
acquisition stays caller-owned at the Phase 2 boundary; the declared dataset
is the only input.

Deterministic semantics
-----------------------

Identical dataset and configuration produce identical frame sequences across
independent sources; every declared candle is processed exactly once in
order; ``update()`` past the declared history raises through the frozen
replay machinery; and the sequence is prefix-stable — the first ``k`` frames
of a complete history equal all frames of the corresponding declared prefix,
because no future candle can influence a published fact. That stability is
exactly what Phase 26E recovery depends on when regenerating history.
"""

from __future__ import annotations

from smcsignal.analysis.backtest import BacktestConfiguration, HistoricalReplay, ReplayDataset
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.signal_engine.models import SignalSnapshot


class SeriesFrameSource:
    """One chronological, caller-driven source of real publication frames.

    Wraps a single frozen Phase 20 ``HistoricalReplay`` over the declared
    dataset and projects each replayed step to its published Phase 17
    ``SignalSnapshot`` — nothing else. Each ``update()`` draws the next
    declared candle through the unchanged Phase 3-17 chain and returns the
    frame the engine actually published; ``frames()`` drains the remaining
    declared history and is identical to the equivalent repeated ``update()``
    calls. All input validation, chronological ordering, availability gating,
    and exhaustion behavior is the frozen replay's — this class adds none and
    weakens none.
    """

    def __init__(self, dataset: ReplayDataset, configuration: BacktestConfiguration) -> None:
        self._replay = HistoricalReplay(dataset, configuration)

    @property
    def dataset(self) -> ReplayDataset:
        """The declared history this source draws from."""

        return self._replay.dataset

    @property
    def configuration(self) -> BacktestConfiguration:
        """The frozen pipeline configuration the frames are produced under."""

        return self._replay.configuration

    @property
    def series(self) -> SeriesProvenance:
        """The series identity of the declared dataset."""

        return self._replay.series

    @property
    def processed_count(self) -> int:
        """How many declared candles have been drawn so far."""

        return self._replay.processed_count

    @property
    def complete(self) -> bool:
        """True exactly when every declared candle has been drawn."""

        return self._replay.complete

    def update(self) -> SignalSnapshot:
        """Draw the next declared candle; return the real published frame.

        After the declared history is exhausted this raises through the frozen
        replay machinery — there is no sentinel frame and no silent end.
        """

        return self._replay.update().signal

    def frames(self) -> tuple[SignalSnapshot, ...]:
        """Drain the remaining declared history; identical to repeated updates."""

        collected: list[SignalSnapshot] = []
        while not self._replay.complete:
            collected.append(self.update())
        return tuple(collected)


def series_frames(
    dataset: ReplayDataset, configuration: BacktestConfiguration
) -> tuple[SignalSnapshot, ...]:
    """Batch regeneration of one declared history; identical to any chunked drain."""

    return SeriesFrameSource(dataset, configuration).frames()
