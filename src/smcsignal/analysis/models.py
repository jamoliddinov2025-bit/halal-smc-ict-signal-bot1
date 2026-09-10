"""Immutable, causally timestamped swings, trend states, and structure events.

Timestamp fields identify candle OPEN times, as in OHLCV. Results are available
only AFTER the referenced observation/confirmation candle has closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.data.models import EPOCH


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class TrendDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"


class StructureEventKind(StrEnum):
    BOS = "BOS"
    CHOCH = "CHoCH"


def _index(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise AnalysisInputError(f"{name} must be a nonnegative integer")


def _timestamp(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise AnalysisInputError(f"{name} must be a timezone-aware candle timestamp")
    try:
        result = value.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise AnalysisInputError(f"{name} is outside the supported timestamp range") from exc
    if result < EPOCH or result.microsecond % 1000:
        raise AnalysisInputError(f"{name} must be at millisecond precision after the Unix epoch")
    return result


def _price(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise AnalysisInputError(f"{name} must be a finite positive Decimal")


@dataclass(frozen=True, slots=True)
class Swing:
    """A strict pivot, published at confirmed_index rather than pivot_index."""

    kind: SwingKind
    pivot_index: int
    pivot_timestamp: datetime
    price: Decimal
    confirmed_index: int
    confirmed_timestamp: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SwingKind):
            raise AnalysisInputError("kind must be a SwingKind")
        _index(self.pivot_index, "pivot_index")
        _index(self.confirmed_index, "confirmed_index")
        _price(self.price, "price")
        object.__setattr__(
            self, "pivot_timestamp", _timestamp(self.pivot_timestamp, "pivot_timestamp")
        )
        object.__setattr__(
            self, "confirmed_timestamp", _timestamp(self.confirmed_timestamp, "confirmed_timestamp")
        )
        if (
            self.confirmed_index <= self.pivot_index
            or self.confirmed_timestamp <= self.pivot_timestamp
        ):
            raise AnalysisInputError("confirmation must be strictly later than the pivot")


@dataclass(frozen=True, slots=True)
class TrendState:
    """Latest two confirmed highs/lows and the trend known after this candle."""

    candle_index: int
    timestamp: datetime
    direction: TrendDirection
    swing_highs: tuple[Swing, ...] = ()
    swing_lows: tuple[Swing, ...] = ()

    def __post_init__(self) -> None:
        _index(self.candle_index, "candle_index")
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp, "timestamp"))
        if not isinstance(self.direction, TrendDirection):
            raise AnalysisInputError("direction must be a TrendDirection")
        for values, kind in ((self.swing_highs, SwingKind.HIGH), (self.swing_lows, SwingKind.LOW)):
            if not isinstance(values, tuple) or len(values) > 2:
                raise AnalysisInputError(
                    "trend evidence must be an immutable tuple of at most two swings"
                )
            previous: Swing | None = None
            for swing in values:
                if not isinstance(swing, Swing) or swing.kind != kind:
                    raise AnalysisInputError("swing evidence has the wrong kind")
                if (
                    swing.confirmed_index > self.candle_index
                    or swing.confirmed_timestamp > self.timestamp
                ):
                    raise AnalysisInputError("future/unconfirmed swing evidence is not available")
                if previous is not None and (
                    swing.pivot_index <= previous.pivot_index
                    or swing.confirmed_index <= previous.confirmed_index
                    or swing.pivot_timestamp <= previous.pivot_timestamp
                    or swing.confirmed_timestamp <= previous.confirmed_timestamp
                ):
                    raise AnalysisInputError(
                        "swing evidence must be strictly chronological and unique"
                    )
                previous = swing

    @property
    def ready(self) -> bool:
        """False distinguishes insufficient evidence from an established range."""
        return len(self.swing_highs) == len(self.swing_lows) == 2


@dataclass(frozen=True, slots=True)
class StructureEvent:
    """A one-time closing-price crossing of a previously confirmed swing level."""

    candle_index: int
    timestamp: datetime
    kind: StructureEventKind
    direction: TrendDirection
    level: Swing
    previous_close: Decimal
    close: Decimal
    trend_before: TrendDirection

    def __post_init__(self) -> None:
        _index(self.candle_index, "candle_index")
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp, "timestamp"))
        if not isinstance(self.kind, StructureEventKind):
            raise AnalysisInputError("kind must be a StructureEventKind")
        for name, direction in (("direction", self.direction), ("trend_before", self.trend_before)):
            if not isinstance(direction, TrendDirection) or direction == TrendDirection.RANGING:
                raise AnalysisInputError(
                    f"{name} must be bullish or bearish for a classified event"
                )
        if not isinstance(self.level, Swing):
            raise AnalysisInputError("level must be a confirmed Swing")
        if (
            self.level.confirmed_index >= self.candle_index
            or self.level.confirmed_timestamp >= self.timestamp
        ):
            raise AnalysisInputError("a break must use a level confirmed before this candle")
        _price(self.previous_close, "previous_close")
        _price(self.close, "close")
        if self.direction == TrendDirection.BULLISH:
            crossed = (
                self.level.kind == SwingKind.HIGH
                and self.previous_close <= self.level.price < self.close
            )
        else:
            crossed = (
                self.level.kind == SwingKind.LOW
                and self.previous_close >= self.level.price > self.close
            )
        if not crossed:
            raise AnalysisInputError("event must represent a strict closing-price crossing")
        continuation = self.direction == self.trend_before
        if (self.kind == StructureEventKind.BOS) != continuation:
            raise AnalysisInputError("BOS continues the prior trend; CHoCH opposes it")


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """One immutable output per processed closed candle; swings/events are deltas."""

    candle_index: int
    timestamp: datetime
    confirmed_swings: tuple[Swing, ...]
    trend: TrendState
    events: tuple[StructureEvent, ...]

    def __post_init__(self) -> None:
        _index(self.candle_index, "candle_index")
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp, "timestamp"))
        if not isinstance(self.trend, TrendState) or (
            self.trend.candle_index,
            self.trend.timestamp,
        ) != (self.candle_index, self.timestamp):
            raise AnalysisInputError("trend must belong to this snapshot candle")
        if not isinstance(self.confirmed_swings, tuple) or not isinstance(self.events, tuple):
            raise AnalysisInputError("snapshot collections must be immutable tuples")
        if len(self.confirmed_swings) > 2 or len(self.events) > 1:
            raise AnalysisInputError(
                "a candle may confirm up to two swings and classify at most one break"
            )
        for swing in self.confirmed_swings:
            if not isinstance(swing, Swing) or (
                swing.confirmed_index,
                swing.confirmed_timestamp,
            ) != (self.candle_index, self.timestamp):
                raise AnalysisInputError("swings must be published on their confirmation candle")
        if len({swing.kind for swing in self.confirmed_swings}) != len(self.confirmed_swings):
            raise AnalysisInputError("duplicate swing kind on one confirmation candle")
        for event in self.events:
            if not isinstance(event, StructureEvent) or (event.candle_index, event.timestamp) != (
                self.candle_index,
                self.timestamp,
            ):
                raise AnalysisInputError("events must belong to this snapshot candle")
