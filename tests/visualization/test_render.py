from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from copy import deepcopy
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.setup_attribution import analyze_setup_attribution
from smcsignal.analysis.signal_engine import SignalStatus
from smcsignal.analysis.visualization import (
    EventMarker,
    LevelLine,
    render_signal_explanation,
    render_svg,
    render_text,
)
from tests.outcome_tracking.helpers import signal_frames
from tests.visualization.helpers import model

GOLDEN_TEXT = (
    "BTCUSDT 15m; candles 18; not advice\n"
    "37.00 |   +++++++++++++++\n"
    "36.25 |#                 \n"
    "35.50 | #+         o-----\n"
    "34.75 |                  \n"
    "34.00 |  #++             \n"
    "33.25 |   #++   ---------\n"
    "32.50 |    ^++   .       \n"
    "31.75 |          ++      \n"
    "31.00 |     #++^+ #^     \n"
    "30.25 |      #+ #    ====\n"
    "29.50 |       #      ====\n"
    "28.75 |             +====\n"
    "28.00 |             +====\n"
    "27.25 |              +===\n"
    "26.50 |              ====\n"
    "25.75 |              ++==\n"
    "25.00 |              ====\n"
    "24.25 |             ^ ++ \n"
    "23.50 |              ^   \n"
    "22.75 |                ++\n"
    "22.00 |               #  \n"
    "21.25 |                ^+\n"
    "20.50 |                 #\n"
    "19.75 |                  \n"
    "     ------------------\n"
    "legend: # up close, . down close, - level, = zone, ^ bullish, v bearish, "
    "o neutral, * reference, + overlay\n"
    "primitives: atr, buy signal, displacement bullish, ema3, ema5, fvg bullish, "
    "liquidity sweep sell_side, liquidity swing_low, outcome WIN, score 25, "
    "score 33, score 35, score 37\n"
)


def test_text_render_is_the_golden_character_grid() -> None:
    assert render_text(model()) == GOLDEN_TEXT


def test_text_grid_rows_are_candle_width_with_decimal_bands() -> None:
    lines = render_text(model()).splitlines()
    assert len(lines) == 28
    bands = lines[1:25]
    for band in bands:
        label, grid = band.split("|", 1)
        Decimal(label.rstrip())  # every band label is an exact Decimal literal
        assert len(grid) == 18  # one column per candle
    assert bands[0].split("|")[0].rstrip() == "37.00"
    assert bands[-1].split("|")[0].rstrip() == "19.75"
    assert lines[25] == "     " + "-" * 18


def test_svg_render_is_well_formed_and_deterministic() -> None:
    drawing = model()
    first = render_svg(drawing)
    assert first == render_svg(drawing)
    root = ElementTree.fromstring(first)
    assert root.tag.endswith("svg")
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == (
        "800",
        "400",
        "0 0 800 400",
    )
    assert first.startswith("<svg ")
    assert first.rstrip("\n").endswith("</svg>")
    assert first.count("<circle") >= 7  # sweeps, displacement, buys, outcome
    assert first.count("<rect") >= 19  # background + candles + zones + overlays
    titles = [element.text for element in root.iter() if element.tag.endswith("title")]
    assert titles == ["BTCUSDT 15m drawing"]
    assert "not advice" in first


def test_svg_render_carries_no_float_artifacts() -> None:
    svg = render_svg(model())
    assert "nan" not in svg and "inf" not in svg and "None" not in svg
    # Every numeric attribute is a plain decimal literal, never scientific
    # notation, NaN, or infinity.
    for value in re.findall(r'"(-?[\d.eE+-]+)"', svg):
        assert re.fullmatch(r"-?\d+(\.\d+)?", value), value


def test_renderers_reject_non_models() -> None:
    with pytest.raises(AnalysisInputError):
        render_svg("drawing")  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        render_text("drawing")  # type: ignore[arg-type]


def test_rendering_never_mutates_the_model() -> None:
    drawing = model()
    saved = deepcopy(drawing)
    render_svg(drawing)
    render_text(drawing)
    assert drawing == saved


def test_model_coordinates_are_exact_decimals() -> None:
    drawing = model()
    assert all(isinstance(candle.candle.close, Decimal) for candle in drawing.candles)
    for primitive in drawing.primitives:
        if isinstance(primitive, LevelLine):
            assert type(primitive.price) is Decimal
        elif isinstance(primitive, EventMarker):
            assert type(primitive.price) is Decimal
    assert drawing.drawing_id.startswith("drawing:")
    assert render_text(deepcopy(drawing)) == render_text(drawing)
    assert render_svg(deepcopy(drawing)) == render_svg(drawing)


def buy_frame_with_attribution():
    frames = signal_frames()
    profiles = analyze_setup_attribution(frames)
    buy_index = next(
        index for index, frame in enumerate(frames) if frame.status is SignalStatus.BUY_SIGNAL
    )
    return frames, profiles, buy_index


def test_signal_explanation_lists_published_facts_only() -> None:
    frames, profiles, buy_index = buy_frame_with_attribution()
    text = render_signal_explanation(frames[buy_index], profiles[buy_index].attribution)
    lines = text.splitlines()
    assert lines[0] == "signal BUY_SIGNAL on BTCUSDT 15m"
    assert lines[1] == "candle opened 2024-01-01T09:00:00+00:00"
    assert lines[2] == "score 25 of publish threshold 10"
    assert lines[3].startswith("components: halal 10, mtf 15")
    assert "mss 0" in lines[3] and "breaker_block 0" in lines[3]
    assert lines[4].startswith("reasons: halal_asset")
    assert lines[5] == "setup labels: mtf_bullish"
    assert lines[6] == "combination: mtf_bullish"
    assert lines[-1] == "descriptive explanation of published facts; not advice"


def test_signal_explanation_without_attribution_omits_label_lines() -> None:
    frames, _, buy_index = buy_frame_with_attribution()
    text = render_signal_explanation(frames[buy_index])
    assert "setup labels" not in text
    assert "combination" not in text
    assert "not advice" in text


def test_signal_explanation_validates_its_inputs() -> None:
    frames, profiles, buy_index = buy_frame_with_attribution()
    with pytest.raises(AnalysisInputError):
        render_signal_explanation("frame")  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        render_signal_explanation(frames[buy_index], "attribution")  # type: ignore[arg-type]
    # A different signal's attribution may not explain this frame.
    other = next(
        snapshot.attribution for snapshot in profiles[8:] if snapshot.attribution is not None
    )
    with pytest.raises(AnalysisInputError):
        render_signal_explanation(frames[buy_index], other)
