"""Phase 27 datasets: the durable declared-history store.

This package makes the declared history itself durable: frozen
``ReplayDataset`` values (primary candles, higher-timeframe histories, and
series identity) persist and restore exactly, through the repository's
existing evidence canon and the frozen dataset constructors, under the same
key-safety and atomicity discipline as the Phase 26D ledger store.

It exists because the Phase 26E-26H restart story assumes the caller can
always re-supply the exact history a ledger was verified against; before this
package that history lived only in caller memory or in acquisition. Restored
datasets compose into ``smcsignal.runs.run_declared_history`` unchanged —
this package never runs a pipeline, fetches a candle, persists a
configuration, or touches analytics, persistence, series, sessions, runs,
delivery, monitoring, or Telegram. It is a leaf: durable values in, durable
values out, deterministic offline IO only.
"""

from .dataset_store import (
    DatasetStore,
    FileDatasetStore,
    MemoryDatasetStore,
    dataset_bytes,
    load_dataset_bytes,
)

__all__ = [
    "DatasetStore",
    "FileDatasetStore",
    "MemoryDatasetStore",
    "dataset_bytes",
    "load_dataset_bytes",
]
