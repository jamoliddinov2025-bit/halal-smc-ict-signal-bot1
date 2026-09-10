"""Phase 26H: the deterministic offline composition seam — one declared run.

This package is the deterministic offline *terminus* of the infrastructure
arc: it composes the frozen Phase 26F frame-regeneration seam with the frozen
Phase 26G single-series ledger session so that one declared historical series
runs end-to-end — candles declared, real Phase 17 publication frames
regenerated, and the durable verified session opened — under exactly one
declared pipeline configuration.

Composition, not live infrastructure
--------------------------------------

Nothing here is a live runtime. There is no feed, websocket, exchange,
polling, scheduler, wall clock, candle acquisition, candle persistence,
retry, concurrency, lock, process lifecycle, or restart supervision — a
future live-feed or runtime phase must supply frames through a separate
boundary, and this package is deliberately not that boundary. It owns no
``SeriesFrameSource`` (Phase 26F stays a free-standing leaf composed as a
value producer), no second coordinator, no state machine, and no result or
state model of its own: it returns the Phase 26G ``LedgerSession`` with all
of its provenance and continuation machinery intact.

The invariant 26H makes structural
----------------------------------

Frame generation and outcome evaluation must run under the SAME declared
configuration: the frames are regenerated under the supplied
``BacktestConfiguration``, and the session runs under exactly that
configuration's ``outcome_tracking`` settings. Before this seam, that
consistency was caller convention; here it is structure — one configuration
object in, one verified durable session out.

Deterministic semantics: identical dataset, configuration, store contents,
and key produce the identical session; the only IO is the Phase 26D store
the caller supplied, channeled through the frozen session boundary. One run
describes one series; multiple series mean multiple runs composed by the
caller. This is not execution, a backtest report, or advice.
"""

from __future__ import annotations

from smcsignal.analysis.backtest import BacktestConfiguration, ReplayDataset
from smcsignal.persistence import LedgerStore
from smcsignal.series import series_frames
from smcsignal.sessions import LedgerSession, open_ledger_session


def run_declared_history(
    dataset: ReplayDataset,
    configuration: BacktestConfiguration,
    store: LedgerStore,
    key: str,
) -> LedgerSession:
    """Run one declared series history into its durable, verified session.

    Regenerates the real Phase 17 publication frames for the declared dataset
    through the frozen Phase 26F seam, then opens the Phase 26G ledger session
    under the same declared configuration's ``outcome_tracking`` settings —
    fresh bootstrap when the store holds no entry, verified replay-based
    recovery when it does. All validation, verification, atomicity, and
    persistence semantics are the frozen 26F/26G machinery's; this function
    adds none and weakens none, and it permanently binds frame generation and
    outcome evaluation to one declared configuration.

    Deterministic and offline: identical inputs produce the identical
    session; no clock, feed, network, or concurrency participates. The
    returned session is the live Phase 26G session — continue it with new
    frames, checkpoint it with ``persist()``, and re-run this function after
    any restart with the regenerated history to recover.
    """

    frames = series_frames(dataset, configuration)
    return open_ledger_session(store, key, configuration.outcome_tracking, frames)
