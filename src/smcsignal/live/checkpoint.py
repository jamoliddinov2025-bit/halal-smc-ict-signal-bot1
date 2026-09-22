"""Phase 35E: explicit versioned live checkpoint — durable runtime state.

The checkpoint is the sole Phase 35E analysis-restart artifact. It is not the
Phase 35D Telegram outbox (delivery durability stays there), not the Phase 27
dataset window (bounded raw context may still live there), and not the Phase
26D ledger (outcome durability stays there). Ownership is separate: this
module persists only what the live runtime and service need to resume the
Phase 3-19 chain deterministically. Raw-window eviction is not owned here
either — the single sanctioned raw-context bound is
``smcsignal.live.retention.RetentionPolicy.bound_candles``.

Contents (schema ``live-checkpoint-v1``)
----------------------------------------

- checkpoint schema/version and methodology tag;
- configuration identity (Phase 28 canonical ``configuration_digest``);
- symbol, primary timeframe, configured higher timeframes, series identity;
- last processed primary/HTF timestamps and the runtime frame count;
- the declared retention bound used by the owning service;
- full primary and higher-timeframe candle histories — the recovery state the
  frozen Phase 26G verified ledger open replays from index zero, and the HTF
  rematerialization input for deterministic MTF rebuilds;
- the signal engine's duplicate-publication fencing set (published setup
  identities), sorted canonically — re-verified against the warm-up replay
  on restore so a restart cannot re-publish an already-published setup;
- the latest published signal id (provenance continuity check);
- a content digest over the canonical payload.

Warm-up regeneration, not opaque blob restore
---------------------------------------------

On restore the caller rebuilds the frozen chain by replaying
``primary_candles`` through ``LiveRuntime.warm_up`` (identical to the
existing Phase 26F/26G regeneration path), then re-derives fencing and the
latest snapshot. The checkpoint's stored fencing set and signal id are
*verified against* that regeneration — they are never injected blindly.
A mismatch fails closed.

Canonicalization and integrity
------------------------------

Bytes are produced by the repository's existing evidence canon
(``smcsignal.analysis.liquidity.evidence``: sorted compact JSON, exact
Decimal text, UTC ISO-8601 timestamps). The embedded content digest is
never trusted: ``load_checkpoint_bytes`` re-decodes through the frozen
constructors, re-canonicalizes, and recomputes the digest. Any tampering,
truncation, or schema drift fails closed.

Atomicity
---------

``FileCheckpointStore`` writes a sibling ``.partial`` temporary and swaps it
into place with ``os.replace``. Readers only ever open the exact
``.checkpoint.json`` path — a stale ``.partial`` is never a valid
checkpoint. A failure before the swap leaves the previous checkpoint intact.

Identity validation on restore
------------------------------

A checkpoint is rejected unless schema version, configuration identity,
symbol, primary timeframe, higher-timeframe set, series identity, and the
recomputed integrity digest all match the current runtime declaration.
Mismatches raise :class:`LiveCheckpointError` — never a silent adapt.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Protocol

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.data.models import OHLCV
from smcsignal.live.config import LiveConfigurationError

CHECKPOINT_METHODOLOGY = "live-checkpoint-v1"
CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_KIND = "live-runtime-checkpoint"
_DIGEST_PREFIX = "checkpoint:"
_FILE_SUFFIX = ".checkpoint.json"
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
_DOCUMENT_KEYS = frozenset(
    {
        "methodology",
        "kind",
        "schema_version",
        "configuration_identity",
        "symbol",
        "primary_timeframe",
        "higher_timeframes",
        "series",
        "cursors",
        "retention",
        "primary_candles",
        "higher_candles",
        "published_setups",
        "latest_signal_id",
        "content_digest",
    }
)
_CANDLE_KEYS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})
_SERIES_KEYS = frozenset({"symbol", "timeframe", "venue", "provider", "dataset_id"})


class LiveCheckpointError(LiveConfigurationError):
    """A checkpoint failed schema, identity, or integrity validation."""


def live_checkpoint_key(symbol: str, timeframe: str) -> str:
    """Deterministic Phase 35E checkpoint key for one live series."""

    if (
        not isinstance(symbol, str)
        or not symbol.strip()
        or symbol != symbol.strip()
        or not isinstance(timeframe, str)
        or not timeframe.strip()
        or timeframe != timeframe.strip()
    ):
        raise LiveCheckpointError("symbol and timeframe must be nonempty, trimmed strings")
    return f"checkpoint:live:{symbol}:{timeframe}"


def _validated_key(key: object) -> str:
    """Opaque key mapped to exactly one file inside the store root."""

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


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LiveCheckpointError(f"{name} {_TEXT}")
    return value


def _require_keys(value: object, expected: frozenset[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LiveCheckpointError(f"{name} must be a canonical record object")
    keys = set(value)
    if keys != expected:
        raise LiveCheckpointError(f"{name} must contain exactly: {', '.join(sorted(expected))}")
    return value


def _instant(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise LiveCheckpointError(f"{name} must be an ISO-8601 instant")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LiveCheckpointError(f"{name} is not an ISO-8601 instant") from exc
    if parsed.utcoffset() is None:
        raise LiveCheckpointError(f"{name} must be timezone aware")
    return parsed


def _decode_candle(value: object, name: str) -> OHLCV:
    record = _require_keys(value, _CANDLE_KEYS, name)

    def _decimal(field: str) -> Decimal:
        raw = record[field]
        if not isinstance(raw, str):
            raise LiveCheckpointError(f"{name}.{field} must be canonical Decimal text")
        try:
            result = Decimal(raw)
        except DecimalException as exc:
            raise LiveCheckpointError(f"{name}.{field} is not valid Decimal text") from exc
        if not result.is_finite():
            raise LiveCheckpointError(f"{name}.{field} must be a finite Decimal")
        return result

    try:
        return OHLCV(
            timestamp=_instant(record["timestamp"], f"{name}.timestamp"),
            open=_decimal("open"),
            high=_decimal("high"),
            low=_decimal("low"),
            close=_decimal("close"),
            volume=_decimal("volume"),
        )
    except AnalysisInputError as exc:
        raise LiveCheckpointError(f"{name} failed frozen OHLCV validation: {exc}") from exc


def _candle_payload(candle: OHLCV) -> dict[str, object]:
    return {
        "timestamp": candle.timestamp,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


@dataclass(frozen=True, slots=True)
class LiveCheckpoint:
    """One explicit, versioned live runtime checkpoint (immutable)."""

    configuration_identity: str
    symbol: str
    primary_timeframe: str
    higher_timeframes: tuple[str, ...]
    series: SeriesProvenance
    last_primary: datetime | None
    last_higher: Mapping[str, datetime | None]
    frame_count: int
    retention_local_bound: int
    primary_candles: tuple[OHLCV, ...]
    higher_candles: Mapping[str, tuple[OHLCV, ...]]
    published_setups: tuple[str, ...]
    latest_signal_id: str | None
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    methodology: str = CHECKPOINT_METHODOLOGY

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise LiveCheckpointError(
                f"checkpoint schema_version must be {CHECKPOINT_SCHEMA_VERSION}"
            )
        if self.methodology != CHECKPOINT_METHODOLOGY:
            raise LiveCheckpointError(f"checkpoint methodology must be {CHECKPOINT_METHODOLOGY}")
        _text(self.configuration_identity, "configuration_identity")
        _text(self.symbol, "symbol")
        _text(self.primary_timeframe, "primary_timeframe")
        if not isinstance(self.series, SeriesProvenance):
            raise LiveCheckpointError("checkpoint requires a SeriesProvenance series")
        if self.series.symbol != self.symbol or self.series.timeframe != self.primary_timeframe:
            raise LiveCheckpointError("checkpoint series identity must match symbol/timeframe")
        if (
            not isinstance(self.higher_timeframes, tuple)
            or not self.higher_timeframes
            or not all(isinstance(tf, str) and tf for tf in self.higher_timeframes)
        ):
            raise LiveCheckpointError("higher_timeframes must be a nonempty tuple of strings")
        if set(self.higher_timeframes) != set(self.higher_candles):
            raise LiveCheckpointError(
                "higher_candles must cover exactly the configured higher timeframes"
            )
        if set(self.last_higher) != set(self.higher_timeframes):
            raise LiveCheckpointError(
                "last_higher must cover exactly the configured higher timeframes"
            )
        for timeframe, stamp in self.last_higher.items():
            if stamp is not None and stamp.utcoffset() is None:
                raise LiveCheckpointError(f"last_higher[{timeframe}] must be timezone aware")
        if self.last_primary is not None and self.last_primary.utcoffset() is None:
            raise LiveCheckpointError("last_primary must be timezone aware")
        if type(self.frame_count) is not int or self.frame_count < 0:
            raise LiveCheckpointError("frame_count must be a nonnegative integer")
        if type(self.retention_local_bound) is not int or self.retention_local_bound < 3:
            raise LiveCheckpointError("retention_local_bound must be an integer >= 3")
        if not isinstance(self.primary_candles, tuple) or not self.primary_candles:
            raise LiveCheckpointError("primary_candles must be a nonempty tuple")
        if len(self.primary_candles) != self.frame_count:
            raise LiveCheckpointError("primary_candles must contain exactly frame_count candles")
        for name, history in self.higher_candles.items():
            if not isinstance(history, tuple):
                raise LiveCheckpointError(f"higher_candles[{name!r}] must be a tuple")
        if not isinstance(self.published_setups, tuple) or not all(
            isinstance(item, str) and item for item in self.published_setups
        ):
            raise LiveCheckpointError("published_setups must be a tuple of nonempty strings")
        if tuple(sorted(self.published_setups)) != self.published_setups:
            raise LiveCheckpointError("published_setups must be sorted for canonical form")
        if len(set(self.published_setups)) != len(self.published_setups):
            raise LiveCheckpointError("published_setups must be unique")
        if self.latest_signal_id is not None:
            _text(self.latest_signal_id, "latest_signal_id")


def _content_dict(checkpoint: LiveCheckpoint) -> dict[str, object]:
    return {
        "methodology": checkpoint.methodology,
        "kind": CHECKPOINT_KIND,
        "schema_version": checkpoint.schema_version,
        "configuration_identity": checkpoint.configuration_identity,
        "symbol": checkpoint.symbol,
        "primary_timeframe": checkpoint.primary_timeframe,
        "higher_timeframes": list(checkpoint.higher_timeframes),
        "series": {
            "symbol": checkpoint.series.symbol,
            "timeframe": checkpoint.series.timeframe,
            "venue": checkpoint.series.venue,
            "provider": checkpoint.series.provider,
            "dataset_id": checkpoint.series.dataset_id,
        },
        "cursors": {
            "last_primary": checkpoint.last_primary,
            "last_higher": {
                timeframe: checkpoint.last_higher[timeframe]
                for timeframe in checkpoint.higher_timeframes
            },
            "frame_count": checkpoint.frame_count,
        },
        "retention": {"local_context_bound": checkpoint.retention_local_bound},
        "primary_candles": [_candle_payload(candle) for candle in checkpoint.primary_candles],
        "higher_candles": {
            timeframe: [_candle_payload(candle) for candle in checkpoint.higher_candles[timeframe]]
            for timeframe in checkpoint.higher_timeframes
        },
        "published_setups": list(checkpoint.published_setups),
        "latest_signal_id": checkpoint.latest_signal_id,
    }


def _checkpoint_identity(content: dict[str, object]) -> str:
    return _DIGEST_PREFIX + digest(content)


def checkpoint_bytes(checkpoint: LiveCheckpoint) -> bytes:
    """Canonical bytes of one checkpoint; embeds the recomputed content digest."""

    if not isinstance(checkpoint, LiveCheckpoint):
        raise AnalysisInputError("checkpoint_bytes requires a LiveCheckpoint")
    content = _content_dict(checkpoint)
    document = dict(content)
    document["content_digest"] = _checkpoint_identity(content)
    return canonical_bytes(document)


def load_checkpoint_bytes(data: bytes) -> LiveCheckpoint:
    """Restore a checkpoint from canonical bytes; never trusts the digest.

    Validation order: parse, exact document keys, methodology/kind/schema,
    field decoding through frozen constructors, then the recomputed content
    digest versus the embedded value. Any failure raises — fail closed.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise AnalysisInputError("load_checkpoint_bytes requires canonical bytes")
    try:
        document = json.loads(bytes(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveCheckpointError("checkpoint bytes are not canonical JSON") from exc
    record = _require_keys(document, _DOCUMENT_KEYS, "checkpoint document")
    if record["methodology"] != CHECKPOINT_METHODOLOGY:
        raise LiveCheckpointError(
            f"checkpoint document requires methodology {CHECKPOINT_METHODOLOGY}"
        )
    if record["kind"] != CHECKPOINT_KIND:
        raise LiveCheckpointError(f"checkpoint document kind must be {CHECKPOINT_KIND}")
    if record["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        raise LiveCheckpointError(
            f"unsupported checkpoint schema_version {record['schema_version']!r}; "
            f"expected {CHECKPOINT_SCHEMA_VERSION}"
        )
    embedded_digest = record["content_digest"]
    if not isinstance(embedded_digest, str):
        raise LiveCheckpointError("content_digest must be a string")

    series_value = _require_keys(record["series"], _SERIES_KEYS, "checkpoint series")
    try:
        series = SeriesProvenance(
            _text(series_value["symbol"], "series.symbol"),
            _text(series_value["timeframe"], "series.timeframe"),
            _text(series_value["venue"], "series.venue"),
            _text(series_value["provider"], "series.provider"),
            _text(series_value["dataset_id"], "series.dataset_id"),
        )
    except AnalysisInputError as exc:
        raise LiveCheckpointError(f"checkpoint series identity is invalid: {exc}") from exc

    higher_timeframes_value = record["higher_timeframes"]
    if (
        not isinstance(higher_timeframes_value, list)
        or not higher_timeframes_value
        or not all(isinstance(item, str) and item for item in higher_timeframes_value)
    ):
        raise LiveCheckpointError("higher_timeframes must be a nonempty list of strings")
    higher_timeframes = tuple(higher_timeframes_value)

    cursors = _require_keys(
        record["cursors"], frozenset({"last_primary", "last_higher", "frame_count"}), "cursors"
    )
    last_primary_raw = cursors["last_primary"]
    last_primary: datetime | None = (
        None if last_primary_raw is None else _instant(last_primary_raw, "last_primary")
    )
    last_higher_raw = cursors["last_higher"]
    if not isinstance(last_higher_raw, dict):
        raise LiveCheckpointError("last_higher must be a canonical record object")
    last_higher: dict[str, datetime | None] = {}
    for timeframe, value in last_higher_raw.items():
        if not isinstance(timeframe, str):
            raise LiveCheckpointError("last_higher keys must be timeframe strings")
        last_higher[timeframe] = (
            None if value is None else _instant(value, f"last_higher[{timeframe}]")
        )
    frame_count = cursors["frame_count"]
    if type(frame_count) is not int or frame_count < 0:
        raise LiveCheckpointError("frame_count must be a nonnegative integer")

    retention = _require_keys(record["retention"], frozenset({"local_context_bound"}), "retention")
    retention_bound = retention["local_context_bound"]
    if type(retention_bound) is not int or retention_bound < 3:
        raise LiveCheckpointError("retention.local_context_bound must be an integer >= 3")

    primary_value = record["primary_candles"]
    if not isinstance(primary_value, list):
        raise LiveCheckpointError("primary_candles must be a canonical list")
    primary_candles = tuple(
        _decode_candle(item, f"primary_candles[{index}]")
        for index, item in enumerate(primary_value)
    )
    higher_value = record["higher_candles"]
    if not isinstance(higher_value, dict):
        raise LiveCheckpointError("higher_candles must be a canonical record object")
    higher_candles: dict[str, tuple[OHLCV, ...]] = {}
    for timeframe, history in higher_value.items():
        if not isinstance(timeframe, str) or not isinstance(history, list):
            raise LiveCheckpointError("higher_candles must map timeframe strings to candle lists")
        higher_candles[timeframe] = tuple(
            _decode_candle(item, f"higher_candles[{timeframe}][{index}]")
            for index, item in enumerate(history)
        )

    published_value = record["published_setups"]
    if not isinstance(published_value, list) or not all(
        isinstance(item, str) and item for item in published_value
    ):
        raise LiveCheckpointError("published_setups must be a list of strings")
    published_setups = tuple(published_value)

    latest_raw = record["latest_signal_id"]
    latest_signal_id: str | None
    if latest_raw is None:
        latest_signal_id = None
    elif isinstance(latest_raw, str):
        latest_signal_id = latest_raw if latest_raw else None
    else:
        raise LiveCheckpointError("latest_signal_id must be a string or null")

    # Reconstruct through frozen constructors first (validation), then
    # re-canonicalize and compare digests — the embedded digest is never
    # trusted on its own.
    checkpoint = LiveCheckpoint(
        configuration_identity=_text(record["configuration_identity"], "configuration_identity"),
        symbol=_text(record["symbol"], "symbol"),
        primary_timeframe=_text(record["primary_timeframe"], "primary_timeframe"),
        higher_timeframes=higher_timeframes,
        series=series,
        last_primary=last_primary,
        last_higher=last_higher,
        frame_count=frame_count,
        retention_local_bound=retention_bound,
        primary_candles=primary_candles,
        higher_candles=higher_candles,
        published_setups=published_setups,
        latest_signal_id=latest_signal_id,
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        methodology=CHECKPOINT_METHODOLOGY,
    )
    recomputed = _checkpoint_identity(_content_dict(checkpoint))
    if embedded_digest != recomputed:
        raise LiveCheckpointError(
            "checkpoint failed the recomputed content digest check; "
            "the stored state is corrupt or was tampered with"
        )
    return checkpoint


def verify_checkpoint_identity(checkpoint: LiveCheckpoint, expected: LiveCheckpoint) -> None:
    """Fail closed when a restored checkpoint belongs to another declaration."""

    if not isinstance(checkpoint, LiveCheckpoint) or not isinstance(expected, LiveCheckpoint):
        raise AnalysisInputError("verify_checkpoint_identity requires two LiveCheckpoint values")
    mismatches: list[str] = []
    if checkpoint.schema_version != expected.schema_version:
        mismatches.append("schema_version")
    if checkpoint.configuration_identity != expected.configuration_identity:
        mismatches.append("configuration_identity")
    if checkpoint.symbol != expected.symbol:
        mismatches.append("symbol")
    if checkpoint.primary_timeframe != expected.primary_timeframe:
        mismatches.append("primary_timeframe")
    if checkpoint.higher_timeframes != expected.higher_timeframes:
        mismatches.append("higher_timeframes")
    if checkpoint.series != expected.series:
        mismatches.append("series")
    if mismatches:
        raise LiveCheckpointError(
            "checkpoint does not match the current runtime declaration ("
            + ", ".join(mismatches)
            + "); refusing to restore a checkpoint from another configuration"
        )


class CheckpointStore(Protocol):
    """Store and restore immutable ``LiveCheckpoint`` values by opaque key."""

    def save(self, key: str, checkpoint: LiveCheckpoint) -> None: ...

    def load(self, key: str) -> LiveCheckpoint | None: ...

    def contains(self, key: str) -> bool: ...


class MemoryCheckpointStore:
    """Offline in-memory reference store; load re-validates through the canon."""

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}

    def save(self, key: str, checkpoint: LiveCheckpoint) -> None:
        if not isinstance(checkpoint, LiveCheckpoint):
            raise AnalysisInputError("save requires a LiveCheckpoint")
        self._payloads[_validated_key(key)] = checkpoint_bytes(checkpoint)

    def load(self, key: str) -> LiveCheckpoint | None:
        payload = self._payloads.get(_validated_key(key))
        return None if payload is None else load_checkpoint_bytes(payload)

    def contains(self, key: str) -> bool:
        return _validated_key(key) in self._payloads


class FileCheckpointStore:
    """Filesystem checkpoint boundary: one key, one file, atomic replace.

    Writes land in a sibling ``.partial`` and are swapped in with
    ``os.replace``. Reads open only the exact ``.checkpoint.json`` path, so a
    stale partial from a crash is never observed as a valid checkpoint.
    """

    def __init__(self, root: Path | str) -> None:
        if isinstance(root, str):
            root = Path(root)
        if not isinstance(root, Path):
            raise AnalysisInputError("FileCheckpointStore requires a Path root")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        return self._root / (_validated_key(key) + _FILE_SUFFIX)

    def save(self, key: str, checkpoint: LiveCheckpoint) -> None:
        if not isinstance(checkpoint, LiveCheckpoint):
            raise AnalysisInputError("save requires a LiveCheckpoint")
        destination = self._path_for(key)
        payload = checkpoint_bytes(checkpoint)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + _PARTIAL_SUFFIX)
        try:
            with partial.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(partial, destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def load(self, key: str) -> LiveCheckpoint | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        return load_checkpoint_bytes(path.read_bytes())

    def contains(self, key: str) -> bool:
        return self._path_for(key).is_file()


__all__ = [
    "CHECKPOINT_KIND",
    "CHECKPOINT_METHODOLOGY",
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointStore",
    "FileCheckpointStore",
    "LiveCheckpoint",
    "LiveCheckpointError",
    "MemoryCheckpointStore",
    "checkpoint_bytes",
    "live_checkpoint_key",
    "load_checkpoint_bytes",
    "verify_checkpoint_identity",
]
