"""Deterministic, prefix-only evidence identities and lossless JSON artifacts."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256

from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.config import LiquidityConfig
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    SeriesProvenance,
)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise AnalysisInputError("evidence requires finite decimals")
    if not value:
        return "0e0"
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    end = len(digits)
    while digits[end - 1] == 0:
        end -= 1
        exponent += 1
    return ("-" if sign else "") + "".join(str(d) for d in digits[:end]) + "e" + str(exponent)


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise AnalysisInputError("evidence timestamps must be timezone aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise AnalysisInputError(f"unsupported evidence value: {type(value).__name__}")


def evidence_json(value: object) -> str:
    """Full JSON record, including nested raw facts; Decimal values remain strings."""
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_bytes(value: object) -> bytes:
    return evidence_json(value).encode("utf-8")


def digest(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def configuration_artifact(analysis: AnalysisConfig, liquidity: LiquidityConfig) -> bytes:
    return canonical_bytes(
        {"methodology": "liquidity-v1", "analysis": analysis, "liquidity": liquidity}
    )


def prefix_seed(series: SeriesProvenance) -> bytes:
    return canonical_bytes({"schema": "observed-ohlcv-prefix-v1", "series": series})


def provenance(
    *,
    series: SeriesProvenance,
    producer: str,
    configuration_hash: str,
    input_prefix_hash: str,
    available_at: datetime,
    key: object,
    source_candles: tuple[CandleReference, ...],
    dependencies: tuple[EvidenceReference, ...] = (),
) -> EvidenceProvenance:
    payload = {
        "series": series,
        "producer": producer,
        "producer_version": "1",
        "configuration_hash": configuration_hash,
        "input_prefix_hash": input_prefix_hash,
        "available_at": available_at,
        "key": key,
        "source_candles": source_candles,
        "dependencies": dependencies,
    }
    return EvidenceProvenance(
        evidence_id=f"{producer}:{digest(payload)}",
        series=series,
        producer=producer,
        producer_version="1",
        configuration_hash=configuration_hash,
        input_prefix_hash=input_prefix_hash,
        available_at=available_at,
        source_candles=source_candles,
        dependencies=dependencies,
    )
