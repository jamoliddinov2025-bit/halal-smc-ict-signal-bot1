"""Phase 29 declarations: the durable declared-run binding store.

This package makes the association of the three independently durable inputs
of the frozen Phase 26H composition seam itself durable: one frozen
``DeclaredRunBinding``
records a dataset key, a configuration key, and a ledger key, plus the Phase
27 dataset ``content_digest`` and the Phase 28 ``configuration_digest``
captured at save time. Persistence uses the repository's existing evidence
canon under the same key-safety and atomicity discipline as Phases 26D/27/28.

It exists because Phases 26D, 27, and 28 persist their values independently,
while the binding of those keys as one declared historical run lived only in
caller memory. Restored bindings are values the caller uses with the existing
stores and the frozen Phase 26H seam — this package never runs a pipeline,
loads the other stores, fetches a candle, persists a dataset, configuration,
or ledger, or touches analytics, persistence, series, sessions, runs,
datasets, configurations, delivery, monitoring, or Telegram. It is a leaf:
durable declarations in, durable declarations out, deterministic offline IO
only.
"""

from .run_binding import (
    DeclaredRunBinding,
    FileRunBindingStore,
    MemoryRunBindingStore,
    RunBindingStore,
    binding_bytes,
    load_binding_bytes,
)

__all__ = [
    "DeclaredRunBinding",
    "FileRunBindingStore",
    "MemoryRunBindingStore",
    "RunBindingStore",
    "binding_bytes",
    "load_binding_bytes",
]
