"""Phase 26D: the durable ledger store — an explicit persistence boundary.

This module is the repository's first writer, and it is deliberately the only
one. It persists Phase 26C ``LedgerSnapshot`` records and nothing else,
through the Phase 26C public API alone:

- writing serializes with ``ledger_bytes(snapshot)`` — the store never
  formats, parses, digests, or validates a snapshot itself;
- reading delegates entirely to ``load_ledger_bytes`` — every integrity and
  frozen-model check stays single-sourced in Phase 26C.

There is no second persistence format and no second digest: the bytes on disk
are exactly the Phase 26C canonical document, named by the caller's key.

What this boundary is NOT
-------------------------

- No resume and no continuation: ``load`` returns a Phase 26C snapshot (which
  yields a read-only ``RestoredAnalyticsLedger``); it never rebuilds a live
  observer or lifecycle, resumes an evaluator, ingests frames, or merges
  anything. Evaluator continuation remains a future phase.
- No live operations: no scheduler, polling, feed, clock, or run loop; no
  fleet orchestration; no monitoring or delivery integration.
- No database, network, encryption, compression, retention, rotation, or
  version history: one current snapshot per key, replaced atomically.

Key safety
----------

A key is an opaque caller-supplied identifier (one per series ledger). It is
validated against a closed charset and rejected if it is empty, overly long,
contains separators, traversal fragments, unsafe leading/trailing characters,
or is a reserved device name. A validated key maps to exactly one file inside
the store root — never outside it.

Atomicity
---------

``FileLedgerStore`` writes to a temporary file in the same directory as the
destination and then swaps it into place with ``os.replace``. A reader
therefore observes either the complete previous snapshot or the complete new
one, never a partial write; a failure before the swap leaves the previous
snapshot intact and leaves no residue.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analytics import LedgerSnapshot, ledger_bytes, load_ledger_bytes

_FILE_SUFFIX = ".ledger.json"
_PARTIAL_SUFFIX = ".partial"
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$")
_RESERVED_KEYS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def _validated_key(key: object) -> str:
    """One opaque caller key, safe to map to a single file inside the root.

    The key is never interpreted as a series, symbol, or trading fact; it is
    only a namespace label. Traversal, separators, reserved device names,
    empty/oversized values, and unsafe edges are all rejected outright.
    """

    if not isinstance(key, str):
        raise AnalysisInputError("store key must be a string")
    if not key or len(key) > 128:
        raise AnalysisInputError("store key must be 1-128 characters long")
    if ".." in key:
        raise AnalysisInputError("store key must not contain a traversal fragment")
    if not _KEY_PATTERN.match(key):
        raise AnalysisInputError(
            "store key must start and end with an alphanumeric character and contain "
            "only [A-Za-z0-9._:-]"
        )
    if key.upper() in _RESERVED_KEYS:
        raise AnalysisInputError("store key must not be a reserved device name")
    return key


class LedgerStore(Protocol):
    """Store and restore Phase 26C ledger snapshots by opaque key."""

    def save(self, key: str, snapshot: LedgerSnapshot) -> None: ...

    def load(self, key: str) -> LedgerSnapshot | None: ...

    def contains(self, key: str) -> bool: ...


class MemoryLedgerStore:
    """Offline in-memory reference implementation (the NullSink precedent).

    Holds exactly the Phase 26C canonical bytes per key; ``load`` re-validates
    through ``load_ledger_bytes`` so memory and file stores share one read
    contract. No IO, no clock, fully deterministic.
    """

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}

    def save(self, key: str, snapshot: LedgerSnapshot) -> None:
        if not isinstance(snapshot, LedgerSnapshot):
            raise AnalysisInputError("save requires a Phase 26C LedgerSnapshot")
        self._payloads[_validated_key(key)] = ledger_bytes(snapshot)

    def load(self, key: str) -> LedgerSnapshot | None:
        payload = self._payloads.get(_validated_key(key))
        return None if payload is None else load_ledger_bytes(payload)

    def contains(self, key: str) -> bool:
        return _validated_key(key) in self._payloads


class FileLedgerStore:
    """Explicit filesystem boundary for Phase 26C ledger snapshots.

    One validated key maps to one file under ``root`` whose contents are
    exactly ``ledger_bytes(snapshot)``. Writes are atomic: the payload lands in
    a sibling temporary file first and is swapped into place with
    ``os.replace``; a failure before the swap removes the temporary file,
    leaves the previous snapshot intact, and leaves no residue. Reads delegate
    entirely to ``load_ledger_bytes`` — tampered or truncated files are
    rejected by Phase 26C, not by this store.
    """

    def __init__(self, root: Path | str) -> None:
        if isinstance(root, str):
            root = Path(root)
        if not isinstance(root, Path):
            raise AnalysisInputError("FileLedgerStore requires a Path root")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        return self._root / (_validated_key(key) + _FILE_SUFFIX)

    def save(self, key: str, snapshot: LedgerSnapshot) -> None:
        if not isinstance(snapshot, LedgerSnapshot):
            raise AnalysisInputError("save requires a Phase 26C LedgerSnapshot")
        destination = self._path_for(key)
        payload = ledger_bytes(snapshot)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + _PARTIAL_SUFFIX)
        try:
            partial.write_bytes(payload)
            os.replace(partial, destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def load(self, key: str) -> LedgerSnapshot | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        return load_ledger_bytes(path.read_bytes())

    def contains(self, key: str) -> bool:
        return self._path_for(key).is_file()
