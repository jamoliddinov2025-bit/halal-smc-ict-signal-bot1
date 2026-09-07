"""Architecture-only evidence provenance; no feature detection or quality values.

Future liquidity/sweep records compose EvidenceProvenance with their own raw
facts. Nothing here creates those features, ranks setups, or generates signals.
Availability is a real UTC instant, not a candle-opening-time identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.data.models import EPOCH


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _instant(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise AnalysisInputError(f"{name} must be a timezone-aware availability instant")
    try:
        result = value.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise AnalysisInputError(f"{name} is outside the supported timestamp range") from exc
    if result < EPOCH:
        raise AnalysisInputError(f"{name} must not precede the Unix epoch")
    # Preserve sub-millisecond arrival times; rounding down could expose evidence early.
    return result


@dataclass(frozen=True, slots=True)
class SeriesProvenance:
    """Exact series identity; dataset_id is a stable logical history namespace."""

    symbol: str
    timeframe: str
    venue: str
    provider: str
    dataset_id: str

    def __post_init__(self) -> None:
        for name in ("symbol", "timeframe", "venue", "provider", "dataset_id"):
            _text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class CandleReference:
    """A source candle; closed_at is its exclusive completed-bar boundary.

    The producer supplies this boundary from authoritative interval metadata,
    never by peeking at the next row. It is not the Phase 3 opening timestamp.
    """

    series: SeriesProvenance
    candle_index: int
    opened_at: datetime
    closed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesProvenance):
            raise AnalysisInputError("series must be SeriesProvenance")
        if type(self.candle_index) is not int or self.candle_index < 0:
            raise AnalysisInputError("candle_index must be a nonnegative integer")
        object.__setattr__(self, "opened_at", _instant(self.opened_at, "opened_at"))
        object.__setattr__(self, "closed_at", _instant(self.closed_at, "closed_at"))
        if self.closed_at <= self.opened_at:
            raise AnalysisInputError("closed_at must follow opened_at")


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """Reference an exact immutable evidence snapshot, never an entity's 'latest'."""

    evidence_id: str
    series: SeriesProvenance
    available_at: datetime

    def __post_init__(self) -> None:
        _text(self.evidence_id, "evidence_id")
        if not isinstance(self.series, SeriesProvenance):
            raise AnalysisInputError("series must be SeriesProvenance")
        object.__setattr__(self, "available_at", _instant(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    """Versioned origin and causality metadata, independent of feature-specific facts.

    IDs and SHA-256 digests are caller-supplied, content-addressable references.
    The producer must retain their source/configuration artifacts and fingerprint
    only information available at this cutoff, not a file containing future rows.
    """

    evidence_id: str
    series: SeriesProvenance
    producer: str
    producer_version: str
    configuration_hash: str
    input_prefix_hash: str
    available_at: datetime
    source_candles: tuple[CandleReference, ...] = ()
    dependencies: tuple[EvidenceReference, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in ("evidence_id", "producer", "producer_version"):
            _text(getattr(self, name), name)
        if not isinstance(self.series, SeriesProvenance):
            raise AnalysisInputError("series must be SeriesProvenance")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AnalysisInputError("unsupported provenance schema_version")
        for name in ("configuration_hash", "input_prefix_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise AnalysisInputError(f"{name} must be a lowercase SHA-256 digest")
        object.__setattr__(self, "available_at", _instant(self.available_at, "available_at"))
        if not isinstance(self.source_candles, tuple) or not isinstance(self.dependencies, tuple):
            raise AnalysisInputError("provenance collections must be immutable tuples")
        previous: CandleReference | None = None
        for candle in self.source_candles:
            if not isinstance(candle, CandleReference) or candle.series != self.series:
                raise AnalysisInputError("source candles must belong to the declared series")
            if candle.closed_at > self.available_at:
                raise AnalysisInputError("source candle is not closed at the evidence cutoff")
            if previous is not None and (
                candle.candle_index <= previous.candle_index
                or candle.opened_at <= previous.opened_at
            ):
                raise AnalysisInputError("source candles must be chronological and unique")
            previous = candle
        seen: set[str] = set()
        for dependency in self.dependencies:
            if not isinstance(dependency, EvidenceReference):
                raise AnalysisInputError("dependencies must be EvidenceReference records")
            if dependency.evidence_id == self.evidence_id or dependency.evidence_id in seen:
                raise AnalysisInputError("self-references and duplicate dependencies are forbidden")
            if dependency.available_at > self.available_at:
                raise AnalysisInputError("dependency is not available at the evidence cutoff")
            seen.add(dependency.evidence_id)

    def as_reference(self) -> EvidenceReference:
        """Keep the exact identity, source context, and knowable instant."""
        return EvidenceReference(self.evidence_id, self.series, self.available_at)


class ProvenancedEvidence(Protocol):
    """Structural contract for future immutable raw-feature records."""

    @property
    def provenance(self) -> EvidenceProvenance:
        """Origin and availability metadata; not an evaluation result."""
        ...
