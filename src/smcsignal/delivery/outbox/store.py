"""Phase 35D: the durable file store for delivery outbox records.

This store follows the repository's established persistence precedent exactly
(``FileLedgerStore`` / ``FileDatasetStore``): a validated key maps to exactly
one file inside the store root; writes go to a ``.partial`` temporary file in
the destination directory and are then swapped into place with ``os.replace``,
so a reader observes either the complete previous document or the complete new
one — never a partial write. Standard library only; no database.

Layout::

    {root}/
      meta.outbox.json                    # bootstrap activation marker
      records/{digest}.outbox.json        # one record per delivery identity
      records/{digest}.outbox.json.partial
      corrupt/{name}.corrupt              # quarantined bytes, never edited

Corruption policy: a document that fails strict validation is quarantined by
an atomic rename into ``corrupt/`` and counted; the store continues serving
every other record. Nothing is ever silently dropped and nothing is ever
rewritten by the quarantine path. Determinism: identical bytes always produce
the identical decision.

The store performs no delivery, no retry, and no scheduling; it is pure
durable bytes plus strict validation.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from smcsignal.analysis.errors import AnalysisInputError

from .models import (
    META_SCHEMA,
    OutboxRecord,
    meta_document,
    parse_meta_document,
)

RECORD_SUFFIX = ".outbox.json"
PARTIAL_SUFFIX = ".partial"
CORRUPT_SUFFIX = ".corrupt"
META_NAME = "meta.outbox.json"
RECORDS_DIR = "records"
CORRUPT_DIR = "corrupt"

#: A record key is the frozen ``delivery_id``: the ``delivery:`` prefix plus
#: the sha256 hex digest from ``smcsignal.delivery.identity`` (exactly 64
#: lowercase hexadecimal characters). No other key shape is accepted.
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OutboxStoreError(RuntimeError):
    """A durable store condition that refuses the requested operation.

    Raised when the activation marker exists but cannot be trusted (the
    reconciliation fail-closed rule): never crash the service, never guess an
    activation time, and never risk re-broadcasting historical signals.
    """


def key_digest(delivery_id: str) -> str:
    """Validate one ``delivery_id`` and return its filename-safe digest."""
    if not isinstance(delivery_id, str) or not delivery_id.startswith("delivery:"):
        raise AnalysisInputError("outbox keys must carry the delivery: prefix")
    digest = delivery_id[len("delivery:") :]
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise AnalysisInputError(
            "outbox keys must be the delivery: prefix plus a sha256 hex digest"
        )
    return digest


class FileOutboxStore:
    """Durable outbox records on disk; atomic writes; strict loads.

    Constructing the store creates the layout when absent and sweeps stale
    ``.partial`` artifacts left by an interrupted write. No meta document is
    created until ``ensure_meta`` is called, so a store whose construction was
    never followed by a real-mode startup leaves no activation marker behind.
    """

    def __init__(self, root: str | Path) -> None:
        resolved = Path(root)
        if not str(resolved).strip():
            raise AnalysisInputError("the outbox store requires a nonempty root")
        self._root = resolved
        self._records = resolved / RECORDS_DIR
        self._corrupt = resolved / CORRUPT_DIR
        self._records.mkdir(parents=True, exist_ok=True)
        self._corrupt.mkdir(parents=True, exist_ok=True)
        self._sweep_partials()
        self._quarantine_count = 0
        self._meta_failure: str | None = None

    # -- operator visibility ------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def quarantine_count(self) -> int:
        """How many documents were quarantined by this store instance."""
        return self._quarantine_count

    @property
    def meta_failure(self) -> str | None:
        """The reason the activation marker is untrusted, if any."""
        return self._meta_failure

    # -- meta --------------------------------------------------------------------------

    def ensure_meta(self, now: datetime) -> datetime:
        """Return the store's ``activated_at``, creating the marker if absent.

        First-ever bootstrap writes ``smcsignal.outbox.meta/v1`` with the
        supplied moment; later startups read the persisted marker unchanged.
        An existing marker that fails validation is quarantined and
        ``OutboxStoreError`` is raised — reconciliation must fail closed
        rather than guess an activation boundary.
        """
        meta_path = self._root / META_NAME
        if not meta_path.exists():
            document = meta_document(now)
            self._atomic_write(meta_path, document)
            return parse_meta_document(document)
        try:
            loaded = self._read_document(meta_path)
            return parse_meta_document(loaded)
        except (AnalysisInputError, OSError, ValueError) as exc:
            self._quarantine(meta_path)
            reason = f"activation marker untrusted: {exc}"
            self._meta_failure = reason
            raise OutboxStoreError(reason) from exc

    # -- records ------------------------------------------------------------------------

    def save_record(self, record: OutboxRecord) -> None:
        """Atomically persist one record (full rewrite of its document)."""
        if not isinstance(record, OutboxRecord):
            raise AnalysisInputError("save_record requires an OutboxRecord")
        self._atomic_write(self._record_path(record.delivery_id), record.to_document())

    def load_record(self, delivery_id: str) -> OutboxRecord | None:
        """Load one record; ``None`` when absent; quarantine when invalid."""
        path = self._record_path(delivery_id)
        if not path.exists():
            return None
        try:
            document = self._read_document(path)
            record = OutboxRecord.from_document(document)
        except (AnalysisInputError, OSError, ValueError):
            self._quarantine(path)
            self._quarantine_count += 1
            return None
        if record.delivery_id != delivery_id:
            self._quarantine(path)
            self._quarantine_count += 1
            return None
        return record

    def all_records(self) -> tuple[OutboxRecord, ...]:
        """Load every valid record, deterministically ordered by delivery id."""
        found: list[OutboxRecord] = []
        for path in sorted(self._records.glob(f"*{RECORD_SUFFIX}")):
            delivery_id = "delivery:" + path.name[: -len(RECORD_SUFFIX)]
            record = self.load_record(delivery_id)
            if record is not None:
                found.append(record)
        found.sort(key=lambda item: item.delivery_id)
        return tuple(found)

    def quarantined_ids(self) -> frozenset[str]:
        """Delivery ids whose record documents were quarantined on disk."""
        ids: set[str] = set()
        suffix = RECORD_SUFFIX + CORRUPT_SUFFIX
        for path in self._corrupt.glob(f"*{suffix}"):
            digest = path.name[: -len(suffix)]
            if _DIGEST_PATTERN.fullmatch(digest):
                ids.add("delivery:" + digest)
        return frozenset(ids)

    # -- mechanics ------------------------------------------------------------------------

    def _record_path(self, delivery_id: str) -> Path:
        return self._records / (key_digest(delivery_id) + RECORD_SUFFIX)

    def _sweep_partials(self) -> None:
        for partial in self._records.glob(f"*{RECORD_SUFFIX}{PARTIAL_SUFFIX}"):
            try:
                partial.unlink()
            except OSError:
                pass

    def _read_document(self, path: Path) -> object:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def _atomic_write(self, destination: Path, document: object) -> None:
        partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        with partial.open("w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(partial, destination)

    def _quarantine(self, path: Path) -> None:
        target = self._corrupt / (path.name + CORRUPT_SUFFIX)
        try:
            os.replace(path, target)
        except OSError:
            try:
                path.unlink()
            except OSError:
                pass

    def __iter__(self) -> Iterator[OutboxRecord]:  # pragma: no cover - convenience
        return iter(self.all_records())


__all__ = [
    "CORRUPT_DIR",
    "CORRUPT_SUFFIX",
    "FileOutboxStore",
    "META_NAME",
    "META_SCHEMA",
    "OutboxStoreError",
    "PARTIAL_SUFFIX",
    "RECORDS_DIR",
    "RECORD_SUFFIX",
    "key_digest",
]
