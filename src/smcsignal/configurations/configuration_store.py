"""Phase 28: the durable declared-configuration store — an explicit persistence boundary.

The Phase 26E–26H restart story is only as complete as its least durable
input. Frame regeneration (26F), verified recovery (26E/26G), and the offline
composition seam (26H) all run under one declared ``BacktestConfiguration`` —
yet before this phase that configuration lived only in caller memory, in TOML
examples, or in Phase 20 loader calls: a real restart could lose it or force a
nondeterministic re-declaration, and a wrong re-declaration makes 26G refuse
the stored ledger atomically. Phase 28 closes the last non-durable input of
the declared run: one frozen ``BacktestConfiguration`` per validated key,
saved and restored exactly.

Serialization canon (no second canon)
-------------------------------------

This module invents no serialization convention. Bytes are produced by the
repository's existing evidence canon (``smcsignal.analysis.liquidity.evidence``:
sorted compact JSON, exact ``Decimal`` text, enum values,
frozen-dataclass-friendly values) — the same canon identities have used since
Phase 3, Phase 26C reused for ledger snapshots, and Phase 27 reused for
declared datasets. The configuration document is an explicit mirror of the
frozen Phase 20 ``configuration_artifact`` field set — the ``backtest`` table,
every nested pipeline table, and the role declarations — and restoration
reconstructs every table through the actual frozen constructors
(``AnalysisConfig``, ``LiquidityConfig``, …, then ``BacktestConfiguration``),
so load-time validation *is* the frozen validation — exact table keys, strict
types, ranges, enum pins, and the publish-threshold cross-check run
unmodified. Nothing is re-implemented here.

Integrity (the embedded digest is never trusted)
------------------------------------------------

The document embeds a content digest computed over the canonical content. On
load the content is decoded, reconstructed through the frozen constructors,
re-canonicalized from the reconstructed configuration, and the digest is
recomputed and compared with the embedded value. Any tampering either fails
schema or frozen-constructor validation or produces a digest mismatch.

Role declarations
-----------------

The document carries the same role facts the frozen Phase 20 artifact
declares: historical replay only, no live trading, no execution, no
optimization, no self-modification, and no advice. They are constants — never
parsed into settings — and any deviation is rejected on load.

Supported value domain
----------------------

The frozen configuration tables use exactly six value shapes — booleans,
exact integers, exact ``Decimal`` values, text, string tuples, and enums —
plus one nullable table (``analysis``). The decoder supports that domain and
rejects everything else; a frozen config that grows a new value shape in a
future phase fails loudly here until that phase extends the mirror.

What this boundary is NOT
-------------------------

- No candle acquisition: fetching, downloading, or polling candles remains
  caller-owned Phase 2 territory; this store persists values the caller
  already declared.
- No dataset or ledger persistence: those stay with the frozen Phase 27
  dataset store and Phase 26D ledger store; keys here bind nothing.
- No append, merge, versioning, retention, or rotation: one current
  configuration per key, replaced atomically.
- No live operations: no scheduler, feed, websocket, run loop, clock, or
  fleet orchestration; deterministic offline IO only.
- No database, network, encryption, or compression.
- No composition: configurations never imports analytics, persistence,
  series, sessions, runs, datasets, delivery, monitoring, or Telegram, and it
  never runs a pipeline; callers compose restored configurations into Phase
  26H themselves.

Key safety
----------

A key is an opaque caller-supplied identifier. It is validated against the
same closed rules as the Phase 26D ledger store and the Phase 27 dataset
store (re-derived here because those validators are private to frozen
modules): empty, oversized, separator, traversal, unsafe-edge, and
reserved-device-name keys are rejected. A validated key maps to exactly one
file inside the store root — never outside it. The key is never interpreted
as a series, symbol, or trading fact.

Atomicity
---------

``FileConfigurationStore`` writes to a temporary file in the same directory
as the destination and then swaps it into place with ``os.replace``. A reader
therefore observes either the complete previous configuration or the complete
new one, never a partial write; a failure before the swap leaves the previous
configuration intact and leaves no residue.
"""

from __future__ import annotations

import json
import os
import re
import types
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, Union, get_args, get_origin, get_type_hints

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.backtest.config import BacktestConfig
from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.displacement.config import DisplacementConfig
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.config import FVGConfig
from smcsignal.analysis.halal_filter.config import HalalFilterConfig
from smcsignal.analysis.liquidity.config import LiquidityConfig
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.analysis.mtf.config import MTFConfig
from smcsignal.analysis.order_blocks.config import OrderBlockConfig
from smcsignal.analysis.ote.config import OTEConfig
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.performance.config import PerformanceConfig
from smcsignal.analysis.premium_discount.config import PDConfig
from smcsignal.analysis.setup_attribution.config import SetupAttributionConfig
from smcsignal.analysis.setup_quality.config import SetupQualityConfig
from smcsignal.analysis.signal_eligibility.config import SignalEligibilityConfig
from smcsignal.analysis.signal_engine.config import SignalEngineConfig

METHODOLOGY_VERSION = "declared-configuration-v1"
_CONFIGURATION_KIND = "declared-configuration"
_DIGEST_PREFIX = "configuration:"
_FILE_SUFFIX = ".configuration.json"
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

#: The pipeline tables of ``BacktestConfiguration``, in the frozen Phase 20
#: ``configuration_artifact`` order. ``analysis`` is the only nullable table.
_PIPELINE_TABLES: tuple[tuple[str, type[Any]], ...] = (
    ("analysis", AnalysisConfig),
    ("liquidity", LiquidityConfig),
    ("displacement", DisplacementConfig),
    ("fvg", FVGConfig),
    ("order_blocks", OrderBlockConfig),
    ("premium_discount", PDConfig),
    ("ote", OTEConfig),
    ("mtf", MTFConfig),
    ("halal_filter", HalalFilterConfig),
    ("setup_quality", SetupQualityConfig),
    ("signal_eligibility", SignalEligibilityConfig),
    ("signal_engine", SignalEngineConfig),
    ("outcome_tracking", OutcomeTrackingConfig),
    ("setup_attribution", SetupAttributionConfig),
    ("performance", PerformanceConfig),
)
_NULLABLE_TABLES = frozenset({"analysis"})

#: The role declarations of the frozen Phase 20 artifact. Constants, never
#: parsed into settings; any deviation is rejected on load.
_ROLE_DECLARATIONS: tuple[tuple[str, object], ...] = (
    ("role", "historical_replay_only"),
    ("live_trading", False),
    ("execution", False),
    ("optimization", False),
    ("self_modification", False),
    ("advice", False),
)


def _validated_key(key: object) -> str:
    """One opaque caller key, safe to map to a single file inside the root.

    The same closed rules as the Phase 26D ledger store and the Phase 27
    dataset store: the key is never interpreted as a series, symbol, or
    trading fact; it is only a namespace label. Traversal, separators,
    reserved device names, empty/oversized values, and unsafe edges are all
    rejected outright.
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


def _content_dict(configuration: BacktestConfiguration) -> dict[str, object]:
    content: dict[str, object] = {
        "methodology": METHODOLOGY_VERSION,
        "kind": _CONFIGURATION_KIND,
        "backtest": configuration.backtest,
        "pipeline": {name: getattr(configuration, name) for name, _ in _PIPELINE_TABLES},
    }
    content.update(_ROLE_DECLARATIONS)
    return content


def _configuration_identity(content: dict[str, object]) -> str:
    return _DIGEST_PREFIX + digest(content)


def configuration_bytes(configuration: BacktestConfiguration) -> bytes:
    """Canonical bytes of one declared configuration, through the evidence canon.

    The document embeds the content digest; ``load_configuration_bytes``
    recomputes it rather than trusting the embedded value.
    """

    if not isinstance(configuration, BacktestConfiguration):
        raise AnalysisInputError("configuration_bytes requires a BacktestConfiguration")
    content = _content_dict(configuration)
    document = dict(content)
    document["configuration_digest"] = _configuration_identity(content)
    return canonical_bytes(document)


def _decode_value(annotation: object, value: object, name: str) -> object:
    """Decode one canonical value against its frozen field annotation.

    The supported domain is exactly the one the frozen configuration tables
    use: booleans, exact integers, exact ``Decimal`` text, text, string
    tuples, enums, an optional marker, and nested frozen dataclasses. The
    decoded value is returned for the frozen constructor to validate; no rule
    of the target configuration is checked here.
    """

    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        options = get_args(annotation)
        if len(options) == 2 and type(None) in options:
            inner = options[0] if options[1] is type(None) else options[1]
            return None if value is None else _decode_value(inner, value, name)
        raise AnalysisInputError(f"{name} uses an unsupported union annotation")
    if annotation is bool:
        if type(value) is not bool:
            raise AnalysisInputError(f"{name} must be a boolean")
        return value
    if annotation is int:
        if type(value) is not int:
            raise AnalysisInputError(f"{name} must be an integer")
        return value
    if annotation is str:
        return _text(value, name)
    if annotation is Decimal:
        if not isinstance(value, str):
            raise AnalysisInputError(f"{name} must be canonical Decimal text")
        try:
            result = Decimal(value)
        except ArithmeticError as exc:
            raise AnalysisInputError(f"{name} is not valid Decimal text") from exc
        if not result.is_finite():
            raise AnalysisInputError(f"{name} must be a finite Decimal")
        return result
    if origin is tuple:
        items = get_args(annotation)
        if not isinstance(value, list):
            raise AnalysisInputError(f"{name} must be a canonical list")
        if len(items) == 2 and items[1] is Ellipsis:
            return tuple(
                _decode_value(items[0], item, f"{name}[{index}]")
                for index, item in enumerate(value)
            )
        if len(items) != len(value):
            raise AnalysisInputError(f"{name} must contain exactly {len(items)} entries")
        return tuple(
            _decode_value(item_type, item, f"{name}[{index}]")
            for index, (item_type, item) in enumerate(zip(items, value, strict=True))
        )
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except ValueError as exc:
            raise AnalysisInputError(f"{name} is not a {annotation.__name__} value") from exc
    if isinstance(annotation, type) and is_dataclass(annotation):
        return _decode_table(value, annotation, name)
    raise AnalysisInputError(f"{name} uses an unsupported field annotation: {annotation!r}")


def _decode_table(value: object, cls: type[Any], name: str) -> Any:
    """Reconstruct one frozen configuration table through its constructor.

    The exact-key discipline mirrors the frozen Phase 3–19 TOML loaders: the
    canonical table must name every field and nothing else, and every value
    reaches the real constructor unchanged by this module.
    """

    record = _require_keys(value, frozenset(field.name for field in fields(cls)), name)
    hints = get_type_hints(cls)
    kwargs = {
        field.name: _decode_value(hints[field.name], record[field.name], f"{name}.{field.name}")
        for field in fields(cls)
    }
    return cls(**kwargs)


_DOCUMENT_KEYS = frozenset(
    {
        "methodology",
        "kind",
        "backtest",
        "pipeline",
        "role",
        "live_trading",
        "execution",
        "optimization",
        "self_modification",
        "advice",
        "configuration_digest",
    }
)


def load_configuration_bytes(data: bytes) -> BacktestConfiguration:
    """Restore a declared configuration from canonical bytes; never trusts the digest.

    Validation order: parse, exact document keys, methodology/kind, the role
    declarations, decode and reconstruction through the frozen constructors
    (which run every frozen configuration rule, including the
    publish-threshold cross-check), then the recomputed content digest versus
    the embedded value. Any tampering or malformed content raises.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise AnalysisInputError("load_configuration_bytes requires canonical bytes")
    try:
        document = json.loads(bytes(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError("configuration bytes are not canonical JSON") from exc
    record = _require_keys(document, _DOCUMENT_KEYS, "configuration document")
    if record["methodology"] != METHODOLOGY_VERSION:
        raise AnalysisInputError(
            f"configuration document requires methodology {METHODOLOGY_VERSION}"
        )
    if record["kind"] != _CONFIGURATION_KIND:
        raise AnalysisInputError(f"configuration document kind must be {_CONFIGURATION_KIND}")
    for role_key, role_value in _ROLE_DECLARATIONS:
        if record[role_key] != role_value:
            raise AnalysisInputError(
                f"configuration document must declare {role_key}={role_value!r}"
            )
    embedded_digest = record["configuration_digest"]
    if not isinstance(embedded_digest, str):
        raise AnalysisInputError("configuration_digest must be a string")
    backtest = _decode_table(record["backtest"], BacktestConfig, "configuration document.backtest")
    pipeline_value = record["pipeline"]
    pipeline = _require_keys(
        pipeline_value,
        frozenset(name for name, _ in _PIPELINE_TABLES),
        "configuration document.pipeline",
    )
    tables: dict[str, Any] = {}
    for table_name, table_class in _PIPELINE_TABLES:
        table_value = pipeline[table_name]
        if table_value is None:
            if table_name not in _NULLABLE_TABLES:
                raise AnalysisInputError(
                    f"configuration document.pipeline.{table_name} must be a canonical table"
                )
            tables[table_name] = None
            continue
        tables[table_name] = _decode_table(
            table_value, table_class, f"configuration document.pipeline.{table_name}"
        )
    restored = BacktestConfiguration(backtest=backtest, **tables)
    recomputed = _configuration_identity(_content_dict(restored))
    if embedded_digest != recomputed:
        raise AnalysisInputError(
            "configuration document failed the recomputed content digest check"
        )
    return restored


class ConfigurationStore(Protocol):
    """Store and restore frozen ``BacktestConfiguration`` values by opaque key."""

    def save(self, key: str, configuration: BacktestConfiguration) -> None: ...

    def load(self, key: str) -> BacktestConfiguration | None: ...

    def contains(self, key: str) -> bool: ...


class MemoryConfigurationStore:
    """Offline in-memory reference implementation (the NullSink precedent).

    Holds exactly the canonical configuration bytes per key; ``load``
    re-validates through ``load_configuration_bytes`` so memory and file
    stores share one read contract. No IO, no clock, fully deterministic.
    """

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}

    def save(self, key: str, configuration: BacktestConfiguration) -> None:
        if not isinstance(configuration, BacktestConfiguration):
            raise AnalysisInputError("save requires a BacktestConfiguration")
        self._payloads[_validated_key(key)] = configuration_bytes(configuration)

    def load(self, key: str) -> BacktestConfiguration | None:
        payload = self._payloads.get(_validated_key(key))
        return None if payload is None else load_configuration_bytes(payload)

    def contains(self, key: str) -> bool:
        return _validated_key(key) in self._payloads


class FileConfigurationStore:
    """Explicit filesystem boundary for declared configurations.

    One validated key maps to one file under ``root`` whose contents are
    exactly ``configuration_bytes(configuration)``. Writes are atomic: the
    payload lands in a sibling temporary file first and is swapped into place
    with ``os.replace``; a failure before the swap removes the temporary
    file, leaves the previous configuration intact, and leaves no residue.
    Reads delegate entirely to ``load_configuration_bytes`` — tampered or
    truncated files are rejected by the canonical document checks, not by
    this store.
    """

    def __init__(self, root: Path | str) -> None:
        if isinstance(root, str):
            root = Path(root)
        if not isinstance(root, Path):
            raise AnalysisInputError("FileConfigurationStore requires a Path root")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        return self._root / (_validated_key(key) + _FILE_SUFFIX)

    def save(self, key: str, configuration: BacktestConfiguration) -> None:
        if not isinstance(configuration, BacktestConfiguration):
            raise AnalysisInputError("save requires a BacktestConfiguration")
        destination = self._path_for(key)
        payload = configuration_bytes(configuration)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + _PARTIAL_SUFFIX)
        try:
            partial.write_bytes(payload)
            os.replace(partial, destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def load(self, key: str) -> BacktestConfiguration | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        return load_configuration_bytes(path.read_bytes())

    def contains(self, key: str) -> bool:
        return self._path_for(key).is_file()
