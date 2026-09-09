from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.visualization import (
    EventMarker,
    IndicatorOverlay,
    LevelLine,
    StyleToken,
    TextAnnotation,
    VisualizationConfig,
    ZoneRect,
    canonical_order,
)
from tests.visualization.helpers import model

PRICE = Decimal("100")


def test_style_tokens_are_semantic_roles_only() -> None:
    assert {token.value for token in StyleToken} == {
        "bullish",
        "bearish",
        "neutral",
        "reference",
        "overlay",
    }


def test_level_line_validation() -> None:
    line = LevelLine(price=PRICE, label="equilibrium", token=StyleToken.REFERENCE)
    assert line.start_index == 0
    for bad in (
        {"price": Decimal(0)},
        {"price": Decimal(-1)},
        {"price": Decimal("NaN")},
        {"price": "100"},  # type: ignore[dict-item]
        {"label": ""},
        {"label": " padded "},
        {"token": "reference"},  # type: ignore[dict-item]
        {"start_index": -1},
        {"start_index": 1.0},  # type: ignore[dict-item]
    ):
        with pytest.raises(AnalysisInputError):
            replace(line, **bad)


def test_zone_rect_validation() -> None:
    zone = ZoneRect(
        lower=Decimal("99"),
        upper=Decimal("101"),
        start_index=3,
        end_index=7,
        label="fvg bullish",
        token=StyleToken.BULLISH,
    )
    assert zone.end_index == 7
    for bad in (
        {"lower": Decimal("101")},  # lower >= upper
        {"upper": Decimal("99")},
        {"lower": Decimal("100"), "upper": Decimal("100")},
        {"end_index": 2},  # before start
        {"end_index": -1},
        {"start_index": -3},
        {"label": ""},
    ):
        with pytest.raises(AnalysisInputError):
            replace(zone, **bad)


def test_marker_and_annotation_and_overlay_validation() -> None:
    marker = EventMarker(index=4, price=PRICE, label="buy signal", token=StyleToken.BULLISH)
    annotation = TextAnnotation(index=4, price=PRICE, text="score 25", token=StyleToken.BULLISH)
    overlay = IndicatorOverlay(index=4, label="ema20", value=Decimal("101.5"))
    assert overlay.token is StyleToken.OVERLAY
    for primitive, bad in (
        (marker, {"index": -1}),
        (marker, {"price": Decimal(0)}),
        (marker, {"label": " "}),
        (annotation, {"text": ""}),
        (annotation, {"index": "4"}),  # type: ignore[dict-item]
        (overlay, {"value": Decimal(-1)}),
        (overlay, {"label": " padded"}),
        (overlay, {"token": "overlay"}),  # type: ignore[dict-item]
    ):
        with pytest.raises(AnalysisInputError):
            replace(primitive, **bad)


def test_canonical_order_sorts_by_rank_then_coordinates() -> None:
    primitives = (
        EventMarker(index=5, price=Decimal("30"), label="buy signal", token=StyleToken.BULLISH),
        LevelLine(price=Decimal("10"), label="pool", token=StyleToken.NEUTRAL),
        ZoneRect(
            lower=Decimal("20"),
            upper=Decimal("22"),
            start_index=2,
            end_index=None,
            label="fvg bullish",
            token=StyleToken.BULLISH,
        ),
        TextAnnotation(index=1, price=Decimal("30"), text="score 9", token=StyleToken.BULLISH),
        IndicatorOverlay(index=0, label="ema3", value=Decimal("21")),
        LevelLine(price=Decimal("9"), label="pool", token=StyleToken.NEUTRAL),
        EventMarker(index=4, price=Decimal("30"), label="buy signal", token=StyleToken.BULLISH),
    )
    ordered = canonical_order(primitives)
    assert [type(item).__name__ for item in ordered] == [
        "LevelLine",
        "LevelLine",
        "ZoneRect",
        "EventMarker",
        "EventMarker",
        "TextAnnotation",
        "IndicatorOverlay",
    ]
    assert [item.price for item in ordered[:2]] == [Decimal("9"), Decimal("10")]
    assert [item.index for item in ordered[3:5]] == [4, 5]


def test_drawing_model_requires_canonical_primitive_order() -> None:
    drawing = model()
    shuffled = tuple(reversed(drawing.primitives))
    with pytest.raises(AnalysisInputError):
        replace(drawing, primitives=shuffled)


def test_drawing_model_validates_the_candle_window() -> None:
    drawing = model()
    with pytest.raises(AnalysisInputError):
        replace(drawing, candles=())
    with pytest.raises(AnalysisInputError):
        replace(drawing, candles=drawing.candles[:-1])  # indices no longer run from zero
    with pytest.raises(AnalysisInputError):
        replace(drawing, symbol=" BTCUSDT")
    with pytest.raises(AnalysisInputError):
        replace(drawing, timeframe="")
    with pytest.raises(AnalysisInputError):
        replace(drawing, settings="strict")  # type: ignore[arg-type]


def test_drawing_model_rejects_primitives_outside_the_window() -> None:
    drawing = model()
    outside = EventMarker(
        index=len(drawing.candles) + 5,
        price=Decimal("100"),
        label="buy signal",
        token=StyleToken.BULLISH,
    )
    with pytest.raises(AnalysisInputError):
        replace(drawing, primitives=(*drawing.primitives, outside))


def test_drawing_id_is_deterministic_and_prefixed() -> None:
    first = model()
    second = model()
    assert first.drawing_id == second.drawing_id
    assert first.drawing_id.startswith("drawing:")
    assert len(first.drawing_id) == len("drawing:") + 64
    assert replace(first, settings=VisualizationConfig(text_rows=12)) != first.settings
