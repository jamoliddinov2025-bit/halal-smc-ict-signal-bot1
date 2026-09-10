"""Phase 26F public API: the deterministic series frame source.

One downstream composition over the frozen Phase 20 replay machinery: given a
declared ``ReplayDataset`` and ``BacktestConfiguration``, ``SeriesFrameSource``
emits the exact real Phase 17 ``SignalSnapshot`` publication stream,
chronologically and deterministically, so the analytics arc can regenerate
frame history through production code instead of test glue.

The package imports only ``smcsignal.analysis.*`` and stdlib. It knows no
ledger, snapshot, store, recovery, delivery, monitoring, clock, or network;
candle acquisition remains caller-owned at the Phase 2 boundary. Nothing here
is a lifecycle, session, coordinator, run loop, scheduler, or application
runtime.
"""

from smcsignal.series.frame_source import SeriesFrameSource, series_frames

__all__ = [
    "SeriesFrameSource",
    "series_frames",
]
