"""Phase 29: the durable declared-run binding — a run-identity persistence boundary.

Phases 26D, 27, and 28 independently persist a ledger, a declared dataset, and
a declared configuration. The association of those three opaque keys as *one*
declared historical run lived only in caller memory: a restart could lose it
or pair the wrong keys. Phase 29 makes that association durable: one frozen
``DeclaredRunBinding`` per validated run-binding key.

What a binding records
----------------------

- ``dataset_key``, ``configuration_key``, ``ledger_key``: opaque persistence
  keys. They are never interpreted as a series, symbol, or trading fact.
- ``dataset_digest``: the canonical Phase 27 dataset document identity — the
  ``content_digest`` field of a declared-dataset document (prefix ``dataset:``
  plus the evidence-canon SHA-256 of the reconstructed dataset content).
- ``configuration_digest``: the canonical Phase 28 configuration document
  identity — the ``configuration_digest`` field of a declared-configuration
  document (prefix ``configuration:`` plus the evidence-canon SHA-256 of the
  reconstructed configuration content).

The ledger identity in the binding *is* the ledger key. Phase 26D remains the
sole writer of ledger documents; this leaf copies no ledger bytes and invents
no second ledger serialization.

The caller supplies the Phase 27/28 identity strings at construction time.
This leaf never loads ``smcsignal.datasets``, ``smcsignal.configurations``,
or ``smcsignal.persistence``, never reconstructs a dataset or configuration,
and never composes a declared history run. Composition stays with the caller:
restore the binding, load the three existing stores by the recorded keys,
then compose through the frozen Phase 26H API.

Serialization canon (no second canon)
-------------------------------------

Bytes are produced by the repository's existing evidence canon
(``smcsignal.analysis.liquidity.evidence``: sorted compact JSON). The document
embeds a content digest; ``load_binding_bytes`` recomputes it rather than
trusting the embedded value.

What this boundary is NOT
-------------------------

- No run execution: it does not create a Run, RunSession, or LedgerSession.
- No composition: declarations never imports analytics, persistence, series,
  sessions, runs, datasets, configurations, delivery, monitoring, or Telegram.
- No candle, dataset, configuration-table, ledger-entry, chart, Telegram, or
  runtime persistence.
- No live operations: no scheduler, feed, websocket, run loop, clock, or fleet.
- No append, merge, versioning, retention, or rotation: one current binding
  per key, replaced atomically.

Key safety and atomicity match Phases 26D/27/28: the same closed key charset,
and ``FileRunBindingStore`` writes a sibling temporary file then ``os.replace``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest

METHODOLOGY_VERSION = "declared-run-binding-v1"
_BINDING_KIND = "declared-run-binding"
_DIGEST_PREFIX = "binding:"
_DATASET_DIGEST_PREFIX = "dataset:"
_CONFIGURATION_DIGEST_PREFIX = "configuration:"
_FILE_SUFFIX = ".binding.json"
_PARTIAL_SUFFIX = ".partial"
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
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

_TEXT = "must be a nonempty, trimmed string"


def _validated_key(key: object) -> str:
    """One opaque caller key, safe to map to a single file inside the root.

    The same closed rules as the Phase 26D ledger store, the Phase 27 dataset
    store, and the Phase 28 configuration store: the key is never interpreted
    as a series, symbol, or trading fact; it is only a namespace label.
    Traversal, separators, reserved device names, empty/oversized values, and
    unsafe edges are all rejected outright.
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


def _require_keys(value: object, expected: frozenset[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AnalysisInputError(f"{name} must be a canonical record object")
    keys = set(value)
    if keys != expected:
        raise AnalysisInputError(f"{name} must contain exactly: {', '.join(sorted(expected))}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} {_TEXT}")
    return value


def _prefixed_digest(value: object, prefix: str, name: str) -> str:
    """Accept only a Phase 27/28 evidence-canon identity string.

    Phase 27 ``content_digest`` is ``dataset:`` plus SHA-256 hex; Phase 28
    ``configuration_digest`` is ``configuration:`` plus SHA-256 hex. This leaf
    records those strings; it does not recompute them from dataset or
    configuration values.
    """

    text = _text(value, name)
    if not text.startswith(prefix):
        raise AnalysisInputError(f"{name} must use the {prefix} identity prefix")
    digest_hex = text[len(prefix) :]
    if not _HEX_DIGEST.match(digest_hex):
        raise AnalysisInputError(f"{name} must be {prefix} followed by a SHA-256 hex digest")
    return text


@dataclass(frozen=True, slots=True)
class DeclaredRunBinding:
    """Frozen declaration of one historical run's three durable inputs.

    Records opaque store keys plus the Phase 27 dataset ``content_digest`` and
    the Phase 28 ``configuration_digest`` captured at save time. It executes
    nothing: the caller restores this value, loads the existing stores, and
    composes through the frozen Phase 26H seam itself.
    """

    dataset_key: str
    configuration_key: str
    ledger_key: str
    dataset_digest: str
    configuration_digest: str

    def __post_init__(self) -> None:
        _validated_key(self.dataset_key)
        _validated_key(self.configuration_key)
        _validated_key(self.ledger_key)
        _prefixed_digest(self.dataset_digest, _DATASET_DIGEST_PREFIX, "dataset_digest")
        _prefixed_digest(
            self.configuration_digest, _CONFIGURATION_DIGEST_PREFIX, "configuration_digest"
        )


def _content_dict(binding: DeclaredRunBinding) -> dict[str, object]:
    return {
        "methodology": METHODOLOGY_VERSION,
        "kind": _BINDING_KIND,
        "dataset_key": binding.dataset_key,
        "configuration_key": binding.configuration_key,
        "ledger_key": binding.ledger_key,
        "dataset_digest": binding.dataset_digest,
        "configuration_digest": binding.configuration_digest,
    }


def _binding_identity(content: dict[str, object]) -> str:
    return _DIGEST_PREFIX + digest(content)


def binding_bytes(binding: DeclaredRunBinding) -> bytes:
    """Canonical bytes of one declared-run binding, through the evidence canon.

    The document embeds the content digest; ``load_binding_bytes`` recomputes
    it rather than trusting the embedded value.
    """

    if not isinstance(binding, DeclaredRunBinding):
        raise AnalysisInputError("binding_bytes requires a DeclaredRunBinding")
    content = _content_dict(binding)
    document = dict(content)
    document["binding_digest"] = _binding_identity(content)
    return canonical_bytes(document)


_DOCUMENT_KEYS = frozenset(
    {
        "methodology",
        "kind",
        "dataset_key",
        "configuration_key",
        "ledger_key",
        "dataset_digest",
        "configuration_digest",
        "binding_digest",
    }
)


def load_binding_bytes(data: bytes) -> DeclaredRunBinding:
    """Restore a declared-run binding from canonical bytes; never trusts the digest.

    Validation order: parse, exact document keys, methodology/kind, reconstruct
    through ``DeclaredRunBinding`` (which runs key and identity-format rules),
    then the recomputed content digest versus the embedded value. Any tampering
    or malformed content raises.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise AnalysisInputError("load_binding_bytes requires canonical bytes")
    try:
        document = json.loads(bytes(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError("binding bytes are not canonical JSON") from exc
    record = _require_keys(document, _DOCUMENT_KEYS, "binding document")
    if record["methodology"] != METHODOLOGY_VERSION:
        raise AnalysisInputError(f"binding document requires methodology {METHODOLOGY_VERSION}")
    if record["kind"] != _BINDING_KIND:
        raise AnalysisInputError(f"binding document kind must be {_BINDING_KIND}")
    embedded_digest = record["binding_digest"]
    if not isinstance(embedded_digest, str):
        raise AnalysisInputError("binding_digest must be a string")
    restored = DeclaredRunBinding(
        dataset_key=_text(record["dataset_key"], "binding document.dataset_key"),
        configuration_key=_text(record["configuration_key"], "binding document.configuration_key"),
        ledger_key=_text(record["ledger_key"], "binding document.ledger_key"),
        dataset_digest=_text(record["dataset_digest"], "binding document.dataset_digest"),
        configuration_digest=_text(
            record["configuration_digest"], "binding document.configuration_digest"
        ),
    )
    recomputed = _binding_identity(_content_dict(restored))
    if embedded_digest != recomputed:
        raise AnalysisInputError("binding document failed the recomputed content digest check")
    return restored


class RunBindingStore(Protocol):
    """Store and restore frozen ``DeclaredRunBinding`` values by opaque key."""

    def save(self, key: str, binding: DeclaredRunBinding) -> None: ...

    def load(self, key: str) -> DeclaredRunBinding | None: ...

    def contains(self, key: str) -> bool: ...


class MemoryRunBindingStore:
    """Offline in-memory reference implementation (the NullSink precedent).

    Holds exactly the canonical binding bytes per key; ``load`` re-validates
    through ``load_binding_bytes`` so memory and file stores share one read
    contract. No IO, no clock, fully deterministic.
    """

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}

    def save(self, key: str, binding: DeclaredRunBinding) -> None:
        if not isinstance(binding, DeclaredRunBinding):
            raise AnalysisInputError("save requires a DeclaredRunBinding")
        self._payloads[_validated_key(key)] = binding_bytes(binding)

    def load(self, key: str) -> DeclaredRunBinding | None:
        payload = self._payloads.get(_validated_key(key))
        return None if payload is None else load_binding_bytes(payload)

    def contains(self, key: str) -> bool:
        return _validated_key(key) in self._payloads


class FileRunBindingStore:
    """Explicit filesystem boundary for declared-run bindings.

    One validated key maps to one file under ``root`` whose contents are
    exactly ``binding_bytes(binding)``. Writes are atomic: the payload lands
    in a sibling temporary file first and is swapped into place with
    ``os.replace``; a failure before the swap removes the temporary file,
    leaves the previous binding intact, and leaves no residue. Reads delegate
    entirely to ``load_binding_bytes`` — tampered or truncated files are
    rejected by the canonical document checks, not by this store.
    """

    def __init__(self, root: Path | str) -> None:
        if isinstance(root, str):
            root = Path(root)
        if not isinstance(root, Path):
            raise AnalysisInputError("FileRunBindingStore requires a Path root")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        return self._root / (_validated_key(key) + _FILE_SUFFIX)

    def save(self, key: str, binding: DeclaredRunBinding) -> None:
        if not isinstance(binding, DeclaredRunBinding):
            raise AnalysisInputError("save requires a DeclaredRunBinding")
        destination = self._path_for(key)
        payload = binding_bytes(binding)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + _PARTIAL_SUFFIX)
        try:
            partial.write_bytes(payload)
            os.replace(partial, destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def load(self, key: str) -> DeclaredRunBinding | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        return load_binding_bytes(path.read_bytes())

    def contains(self, key: str) -> bool:
        return self._path_for(key).is_file()
