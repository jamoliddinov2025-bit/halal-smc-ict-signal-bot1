"""Phase 28 configurations: the durable declared-configuration store.

This package makes the declared configuration itself durable: frozen
``BacktestConfiguration`` values (every nested pipeline table and the role
declarations) persist and restore exactly, through the repository's existing
evidence canon and the frozen configuration constructors, under the same
key-safety and atomicity discipline as the Phase 26D ledger store and the
Phase 27 dataset store.

It exists because the Phase 26E–26H restart story assumes the caller can
always re-supply the exact ``BacktestConfiguration`` every run requires;
before this package that configuration lived only in caller memory, in TOML
examples, or in Phase 20 loader calls. Restored configurations compose into
``smcsignal.runs.run_declared_history`` unchanged — this package never runs a
pipeline, fetches a candle, persists a dataset or ledger, binds keys
together, or touches analytics, persistence, series, sessions, runs,
datasets, delivery, monitoring, or Telegram. It is a leaf: durable values in,
durable values out, deterministic offline IO only.
"""

from .configuration_store import (
    ConfigurationStore,
    FileConfigurationStore,
    MemoryConfigurationStore,
    configuration_bytes,
    load_configuration_bytes,
)

__all__ = [
    "ConfigurationStore",
    "FileConfigurationStore",
    "MemoryConfigurationStore",
    "configuration_bytes",
    "load_configuration_bytes",
]
