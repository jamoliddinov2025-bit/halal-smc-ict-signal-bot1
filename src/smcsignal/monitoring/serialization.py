"""Canonical serialization for monitoring artifacts.

Monitoring records are the primary operator-visible artifact, so they must be
canonical, comparable, and safe to store. This module reuses the project's
existing evidence canon instead of reimplementing it: ``Decimal`` renderings,
UTC ISO-8601 timestamps, enum values, and sorted compact JSON all come from
``smcsignal.analysis.liquidity.evidence``, so a monitoring record and an evidence
record can never disagree about how a value is written.

One rule is stricter than the canon and enforced here: a record may not contain a
``float``, a ``timedelta``, or any other value that has no canonical form. A
duration is always rendered as exact ``Decimal`` milliseconds by the metric that
measured it, and a raw ``timedelta`` reaching a record is a defect rather than
something to coerce. Nothing here writes a file, opens a connection, or logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from smcsignal.analysis.liquidity import evidence
from smcsignal.monitoring.errors import MonitoringInputError


def require_serializable(value: object, name: str = "value") -> None:
    """Raise unless ``value`` has a canonical, deterministic representation.

    Accepts exactly what the evidence canon accepts, minus the values it would
    silently refuse or mishandle: ``None``, ``bool``, ``int``, ``str``, finite
    ``Decimal``, timezone-aware ``datetime``, ``Enum``, string-keyed mappings, and
    tuples or lists of those. ``float`` is rejected rather than rounded, and
    ``timedelta`` is rejected with the reason, because a duration must reach a
    record already rendered as exact milliseconds.
    """
    if value is None or type(value) is bool or type(value) is int:
        return
    if isinstance(value, str):
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise MonitoringInputError(f"{name} contains a nonfinite Decimal")
        return
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise MonitoringInputError(f"{name} contains a naive datetime")
        return
    if isinstance(value, Enum):
        require_serializable(value.value, name)
        return
    if isinstance(value, timedelta):
        raise MonitoringInputError(
            f"{name} contains a timedelta; render durations as exact Decimal milliseconds"
        )
    if isinstance(value, float):
        raise MonitoringInputError(f"{name} contains a float; monitoring records use Decimal")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MonitoringInputError(f"{name} must be keyed by strings")
            require_serializable(item, f"{name}.{key}")
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            require_serializable(item, f"{name}[{index}]")
        return
    raise MonitoringInputError(f"{name} contains an unserializable {type(value).__name__}")


def canonical_record(value: object) -> str:
    """Return compact, sorted, ASCII-only JSON for a validated record."""
    require_serializable(value)
    return evidence.evidence_json(value)


def canonical_bytes(value: object) -> bytes:
    """Return the canonical UTF-8 bytes of a validated record."""
    return canonical_record(value).encode("utf-8")


def content_digest(value: object) -> str:
    """Return the sha256 hex digest of a record's canonical bytes."""
    return evidence.digest(_validated(value))


def _validated(value: object) -> object:
    require_serializable(value)
    return value
