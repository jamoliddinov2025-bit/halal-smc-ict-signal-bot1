"""Immutable drawing primitives over published facts. Colors never appear here."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import digest
from smcsignal.analysis.liquidity.models import ObservedCandle
from smcsignal.analysis.visualization.config import VisualizationConfig


class StyleToken(StrEnum):
    """Semantic style roles; renderers map them, the model never carries colors."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    REFERENCE = "reference"
    OVERLAY = "overlay"


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _price(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise AnalysisInputError(f"{name} must be a positive, finite Decimal price")


def _index(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise AnalysisInputError(f"{name} must be a nonnegative integer candle index")


@dataclass(frozen=True, slots=True)
class LevelLine:
    """A horizontal price level, such as a liquidity pool or equilibrium."""

    price: Decimal
    label: str
    token: StyleToken
    start_index: int = 0

    def __post_init__(self) -> None:
        _price(self.price, "price")
        _text(self.label, "label")
        if not isinstance(self.token, StyleToken):
            raise AnalysisInputError("style must be a StyleToken")
        _index(self.start_index, "start_index")

    def sort_key(self) -> tuple[str, Decimal, str]:
        return ("level", self.price, self.label)


@dataclass(frozen=True, slots=True)
class ZoneRect:
    """A price zone between two boundaries, such as an FVG or order block."""

    lower: Decimal
    upper: Decimal
    start_index: int
    end_index: int | None
    label: str
    token: StyleToken

    def __post_init__(self) -> None:
        _price(self.lower, "lower")
        _price(self.upper, "upper")
        if self.lower >= self.upper:
            raise AnalysisInputError("zone boundaries must satisfy lower < upper")
        _index(self.start_index, "start_index")
        if self.end_index is not None:
            _index(self.end_index, "end_index")
            if self.end_index < self.start_index:
                raise AnalysisInputError("zone end cannot precede its start")
        _text(self.label, "label")
        if not isinstance(self.token, StyleToken):
            raise AnalysisInputError("style must be a StyleToken")

    def sort_key(self) -> tuple[str, int, Decimal, str]:
        return ("zone", self.start_index, self.lower, self.label)


@dataclass(frozen=True, slots=True)
class EventMarker:
    """One event anchored to a candle and price, such as a sweep or BUY signal."""

    index: int
    price: Decimal
    label: str
    token: StyleToken

    def __post_init__(self) -> None:
        _index(self.index, "index")
        _price(self.price, "price")
        _text(self.label, "label")
        if not isinstance(self.token, StyleToken):
            raise AnalysisInputError("style must be a StyleToken")

    def sort_key(self) -> tuple[str, int, Decimal, str]:
        return ("marker", self.index, self.price, self.label)


@dataclass(frozen=True, slots=True)
class TextAnnotation:
    """A short text note anchored to a candle and price."""

    index: int
    price: Decimal
    text: str
    token: StyleToken

    def __post_init__(self) -> None:
        _index(self.index, "index")
        _price(self.price, "price")
        _text(self.text, "text")
        if not isinstance(self.token, StyleToken):
            raise AnalysisInputError("style must be a StyleToken")

    def sort_key(self) -> tuple[str, int, Decimal, str]:
        return ("annotation", self.index, self.price, self.text)


@dataclass(frozen=True, slots=True)
class IndicatorOverlay:
    """One price-scaled indicator point, such as an EMA or ATR value.

    Oscillator- and volume-scaled series (RSI, volume ratio) are not price
    anchored and therefore never enter a price drawing.
    """

    index: int
    label: str
    value: Decimal
    token: StyleToken = StyleToken.OVERLAY

    def __post_init__(self) -> None:
        _index(self.index, "index")
        _text(self.label, "label")
        _price(self.value, "value")
        if not isinstance(self.token, StyleToken):
            raise AnalysisInputError("style must be a StyleToken")

    def sort_key(self) -> tuple[str, int, str, Decimal]:
        return ("overlay", self.index, self.label, self.value)


Primitive = LevelLine | ZoneRect | EventMarker | TextAnnotation | IndicatorOverlay

RANKS = {"level": 0, "zone": 1, "marker": 2, "annotation": 3, "overlay": 4}


def canonical_order(primitives: tuple[Primitive, ...]) -> tuple[Primitive, ...]:
    """The single deterministic ordering used by models and renderers."""

    return tuple(
        sorted(
            primitives,
            key=lambda item: (RANKS[item.sort_key()[0]], *item.sort_key()[1:]),
        )
    )


@dataclass(frozen=True, slots=True)
class DrawingModel:
    """One deterministic drawing over one finished candle window.

    The candles are copied verbatim from the consumed observations. Primitives
    are always stored in canonical order: levels, zones, markers, annotations,
    overlays; each group sorted by its own coordinates. The model carries
    semantic style tokens only. It re-detects nothing and never feeds back into
    signal decisions.
    """

    settings: VisualizationConfig
    symbol: str
    timeframe: str
    candles: tuple[ObservedCandle, ...]
    primitives: tuple[Primitive, ...]
    drawing_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, VisualizationConfig):
            raise AnalysisInputError("drawing requires VisualizationConfig")
        _text(self.symbol, "symbol")
        _text(self.timeframe, "timeframe")
        if not isinstance(self.candles, tuple) or not self.candles:
            raise AnalysisInputError("a drawing covers at least one candle")
        for position, observation in enumerate(self.candles):
            if not isinstance(observation, ObservedCandle):
                raise AnalysisInputError("candles must be ObservedCandle records")
            if observation.reference.candle_index != position:
                raise AnalysisInputError("drawing candles must run from zero consecutively")
            if (
                position
                and observation.reference.opened_at
                <= self.candles[position - 1].reference.opened_at
            ):
                raise AnalysisInputError("drawing candles must be chronological")
        if not isinstance(self.primitives, tuple):
            raise AnalysisInputError("primitives must be a tuple")
        for primitive in self.primitives:
            if not isinstance(
                primitive,
                (LevelLine, ZoneRect, EventMarker, TextAnnotation, IndicatorOverlay),
            ):
                raise AnalysisInputError("primitives must be drawing primitive records")
            for index in _primitive_indices(primitive):
                if index >= len(self.candles):
                    raise AnalysisInputError("primitive indices must lie inside the candle window")
        ordered = canonical_order(self.primitives)
        if self.primitives != ordered:
            raise AnalysisInputError("primitives must be in canonical order")
        object.__setattr__(
            self,
            "drawing_id",
            "drawing:"
            + digest(
                {
                    "methodology": "visualization-v1",
                    "settings": self.settings,
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "candles": tuple(item.reference for item in self.candles),
                    "primitives": self.primitives,
                }
            ),
        )


def _primitive_indices(primitive: Primitive) -> tuple[int, ...]:
    if isinstance(primitive, LevelLine):
        return (primitive.start_index,)
    if isinstance(primitive, ZoneRect):
        return (
            (primitive.start_index,)
            if primitive.end_index is None
            else (
                primitive.start_index,
                primitive.end_index,
            )
        )
    return (primitive.index,)
