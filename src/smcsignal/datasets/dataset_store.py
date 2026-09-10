"""Phase 27: the durable declared-history store — an explicit persistence boundary.

The Phase 26E-26H restart story is only as strong as the availability of the
declared history itself: frame regeneration (26F), verified recovery
(26E/26G), and the offline composition seam (26H) all assume the caller can
always re-supply the exact ``ReplayDataset`` a ledger was verified against.
Before this phase that history lived only in caller memory or in acquisition
(the Phase 2 providers), so a real restart could lose it or force a
nondeterministic re-fetch. Phase 27 makes the declared history itself
durable: one ``ReplayDataset`` per validated key, saved and restored exactly.

Serialization canon (no second canon)
-------------------------------------

This module invents no serialization convention. Bytes are produced by the
repository's existing evidence canon (``smcsignal.analysis.liquidity.evidence``:
sorted compact JSON, exact ``Decimal`` text, UTC ISO-8601 timestamps,
frozen-dataclass-friendly values) — the same canon identities have used since
Phase 3 and Phase 26C reused for ledger snapshots. The dataset document is an
explicit mirror of the frozen ``ReplayDataset`` field set (series identity,
primary candles, higher-timeframe histories), and restoration reconstructs
every candle through the actual frozen constructors (``OHLCV``, then
``ReplayDataset``), so load-time validation *is* the frozen validation —
chronology, uniqueness, non-overlap, timeframe multiples, and every price and
timestamp rule run unmodified. Nothing is re-implemented here.

Integrity (the embedded digest is never trusted)
------------------------------------------------

The document embeds a content digest computed over the canonical content. On
load the content is decoded, reconstructed through the frozen constructors,
re-canonicalized from the reconstructed dataset, and the digest is recomputed
and compared with the embedded value. Any tampering either fails schema or
frozen-constructor validation or produces a digest mismatch.

What this boundary is NOT
-------------------------

- No candle acquisition: fetching, downloading, or polling candles remains
  caller-owned Phase 2 territory; this store only persists values the caller
  already declared.
- No append, merge, versioning, retention, or rotation: one current dataset
  per key, replaced atomically.
- No configuration persistence: ``BacktestConfiguration`` stays caller-
  supplied exactly as Phase 26H requires; this store persists history only.
- No live operations: no scheduler, feed, websocket, run loop, clock, or
  fleet orchestration; deterministic offline IO only.
- No database, network, encryption, or compression.
- No composition: datasets never imports analytics, persistence, series,
  sessions, runs, delivery, monitoring, or Telegram, and it never runs a
  pipeline; callers compose restored datasets into Phase 26H themselves.

Key safety
----------

A key is an opaque caller-supplied identifier. It is validated against the
same closed rules as the Phase 26D ledger store (re-derived here because the
26D validator is private to a frozen module): empty, oversized, separator,
traversal, unsafe-edge, and reserved-device-name keys are rejected. A
validated key maps to exactly one file inside the store root — never outside
it. The key is never interpreted as a series, symbol, or trading fact.

Atomicity
---------

``FileDatasetStore`` writes to a temporary file in the same directory as the
destination and then swaps it into place with ``os.replace``. A reader
therefore observes either the complete previous dataset or the complete new
one, never a partial write; a failure before the swap leaves the previous
dataset intact and leaves no residue.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from smcsignal.analysis.backtest import ReplayDataset
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.data import OHLCV

METHODOLOGY_VERSION = "declared-dataset-v1"
_DATASET_KIND = "declared-dataset"
_DIGEST_PREFIX = "dataset:"
_FILE_SUFFIX = ".dataset.json"
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

_TEXT = "must be a nonempty, trimmed string"


def _validated_key(key: object) -> str:
    """One opaque caller key, safe to map to a single file inside the root.

    The same closed rules as the Phase 26D ledger store: the key is never
    interpreted as a series, symbol, or trading fact; it is only a namespace
    label. Traversal, separators, reserved device names, empty/oversized
    values, and unsafe edges are all rejected outright.
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


def _decimal_text(value: object, name: str) -> Decimal:
    if not isinstance(value, str):
        raise AnalysisInputError(f"{name} must be canonical Decimal text")
    try:
        result = Decimal(value)
    except ArithmeticError as exc:
        raise AnalysisInputError(f"{name} is not valid Decimal text") from exc
    if not result.is_finite():
        raise AnalysisInputError(f"{name} must be a finite Decimal")
    return result


def _instant_text(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise AnalysisInputError(f"{name} must be an ISO-8601 instant")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AnalysisInputError(f"{name} is not a valid ISO-8601 instant") from exc
    if result.utcoffset() is None:
        raise AnalysisInputError(f"{name} must be timezone aware")
    return result


_CANDLE_KEYS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})
_DOCUMENT_KEYS = frozenset(
    {
        "methodology",
        "kind",
        "symbol",
        "timeframe",
        "venue",
        "provider",
        "dataset_id",
        "candles",
        "higher_candles",
        "content_digest",
    }
)


def _candle_payload(candle: OHLCV) -> dict[str, object]:
    return {
        "timestamp": candle.timestamp,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def _content_dict(dataset: ReplayDataset) -> dict[str, object]:
    return {
        "methodology": METHODOLOGY_VERSION,
        "kind": _DATASET_KIND,
        "symbol": dataset.symbol,
        "timeframe": dataset.timeframe,
        "venue": dataset.venue,
        "provider": dataset.provider,
        "dataset_id": dataset.dataset_id,
        "candles": [_candle_payload(candle) for candle in dataset.candles],
        "higher_candles": {
            timeframe: [_candle_payload(candle) for candle in history]
            for timeframe, history in dataset.higher_candles.items()
        },
    }


def _dataset_identity(content: dict[str, object]) -> str:
    return _DIGEST_PREFIX + digest(content)


def dataset_bytes(dataset: ReplayDataset) -> bytes:
    """Canonical bytes of one declared dataset, through the evidence canon.

    The document embeds the content digest; ``load_dataset_bytes`` recomputes
    it rather than trusting the embedded value.
    """

    if not isinstance(dataset, ReplayDataset):
        raise AnalysisInputError("dataset_bytes requires a ReplayDataset")
    content = _content_dict(dataset)
    document = dict(content)
    document["content_digest"] = _dataset_identity(content)
    return canonical_bytes(document)


def _decode_candle(value: object, name: str) -> OHLCV:
    record = _require_keys(value, _CANDLE_KEYS, name)
    return OHLCV(
        timestamp=_instant_text(record["timestamp"], f"{name}.timestamp"),
        open=_decimal_text(record["open"], f"{name}.open"),
        high=_decimal_text(record["high"], f"{name}.high"),
        low=_decimal_text(record["low"], f"{name}.low"),
        close=_decimal_text(record["close"], f"{name}.close"),
        volume=_decimal_text(record["volume"], f"{name}.volume"),
    )


def load_dataset_bytes(data: bytes) -> ReplayDataset:
    """Restore a declared dataset from canonical bytes; never trusts the digest.

    Validation order: parse, exact document keys, methodology/kind, decode
    and reconstruction through the frozen ``OHLCV`` and ``ReplayDataset``
    constructors (which run every frozen data rule), then the recomputed
    content digest versus the embedded value. Any tampering or malformed
    content raises.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise AnalysisInputError("load_dataset_bytes requires canonical bytes")
    try:
        document = json.loads(bytes(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError("dataset bytes are not canonical JSON") from exc
    record = _require_keys(document, _DOCUMENT_KEYS, "dataset document")
    if record["methodology"] != METHODOLOGY_VERSION:
        raise AnalysisInputError(f"dataset document requires methodology {METHODOLOGY_VERSION}")
    if record["kind"] != _DATASET_KIND:
        raise AnalysisInputError(f"dataset document kind must be {_DATASET_KIND}")
    embedded_digest = record["content_digest"]
    if not isinstance(embedded_digest, str):
        raise AnalysisInputError("content_digest must be a string")
    candles_value = record["candles"]
    if not isinstance(candles_value, list):
        raise AnalysisInputError("candles must be a canonical list")
    candles = tuple(
        _decode_candle(item, f"candles[{index}]") for index, item in enumerate(candles_value)
    )
    higher_value = record["higher_candles"]
    if not isinstance(higher_value, dict) or not all(
        isinstance(timeframe, str) for timeframe in higher_value
    ):
        raise AnalysisInputError("higher_candles must map timeframe strings to candle lists")
    higher: dict[str, tuple[OHLCV, ...]] = {}
    for timeframe, history in higher_value.items():
        if not isinstance(history, list):
            raise AnalysisInputError(f"higher_candles[{timeframe}] must be a canonical list")
        higher[timeframe] = tuple(
            _decode_candle(item, f"higher_candles[{timeframe}][{index}]")
            for index, item in enumerate(history)
        )
    dataset = ReplayDataset(
        symbol=_text(record["symbol"], "dataset document.symbol"),
        timeframe=_text(record["timeframe"], "dataset document.timeframe"),
        candles=candles,
        higher_candles=higher,
        venue=_text(record["venue"], "dataset document.venue"),
        provider=_text(record["provider"], "dataset document.provider"),
        dataset_id=_text(record["dataset_id"], "dataset document.dataset_id"),
    )
    recomputed = _dataset_identity(_content_dict(dataset))
    if embedded_digest != recomputed:
        raise AnalysisInputError("dataset document failed the recomputed content digest check")
    return dataset


class DatasetStore(Protocol):
    """Store and restore frozen ``ReplayDataset`` values by opaque key."""

    def save(self, key: str, dataset: ReplayDataset) -> None: ...

    def load(self, key: str) -> ReplayDataset | None: ...

    def contains(self, key: str) -> bool: ...


class MemoryDatasetStore:
    """Offline in-memory reference implementation (the NullSink precedent).

    Holds exactly the canonical dataset bytes per key; ``load`` re-validates
    through ``load_dataset_bytes`` so memory and file stores share one read
    contract. No IO, no clock, fully deterministic.
    """

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}

    def save(self, key: str, dataset: ReplayDataset) -> None:
        if not isinstance(dataset, ReplayDataset):
            raise AnalysisInputError("save requires a ReplayDataset")
        self._payloads[_validated_key(key)] = dataset_bytes(dataset)

    def load(self, key: str) -> ReplayDataset | None:
        payload = self._payloads.get(_validated_key(key))
        return None if payload is None else load_dataset_bytes(payload)

    def contains(self, key: str) -> bool:
        return _validated_key(key) in self._payloads


class FileDatasetStore:
    """Explicit filesystem boundary for declared datasets.

    One validated key maps to one file under ``root`` whose contents are
    exactly ``dataset_bytes(dataset)``. Writes are atomic: the payload lands
    in a sibling temporary file first and is swapped into place with
    ``os.replace``; a failure before the swap removes the temporary file,
    leaves the previous dataset intact, and leaves no residue. Reads delegate
    entirely to ``load_dataset_bytes`` — tampered or truncated files are
    rejected by the canonical document checks, not by this store.
    """

    def __init__(self, root: Path | str) -> None:
        if isinstance(root, str):
            root = Path(root)
        if not isinstance(root, Path):
            raise AnalysisInputError("FileDatasetStore requires a Path root")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        return self._root / (_validated_key(key) + _FILE_SUFFIX)

    def save(self, key: str, dataset: ReplayDataset) -> None:
        if not isinstance(dataset, ReplayDataset):
            raise AnalysisInputError("save requires a ReplayDataset")
        destination = self._path_for(key)
        payload = dataset_bytes(dataset)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + _PARTIAL_SUFFIX)
        try:
            partial.write_bytes(payload)
            os.replace(partial, destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def load(self, key: str) -> ReplayDataset | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        return load_dataset_bytes(path.read_bytes())

    def contains(self, key: str) -> bool:
        return self._path_for(key).is_file()
