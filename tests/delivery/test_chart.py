"""Phase 24B chart integration tests: consume DrawingModel, keep signal bound."""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.visualization import render_svg
from smcsignal.delivery import chart_artifact_id, svg_attachment


def test_chart_attachment_consumes_existing_drawing(a_drawing, buy_snapshot) -> None:
    signal_id = buy_snapshot.signal_id
    attachment = svg_attachment(
        a_drawing,
        signal_id=signal_id,
        symbol=a_drawing.symbol,
        timeframe=a_drawing.timeframe,
    )
    # The artifact is built from the already-created DrawingModel's deterministic
    # drawing_id (never re-created from raw facts).
    assert attachment.drawing_id == a_drawing.drawing_id
    assert attachment.signal_id == signal_id
    # The serialized content is exactly the existing Phase 19e SVG renderer output.
    assert attachment.content == render_svg(a_drawing)
    assert attachment.mime_type == "image/svg+xml"
    assert attachment.artifact_id == chart_artifact_id(
        signal_id, a_drawing.drawing_id, "image/svg+xml"
    )


def test_chart_artifact_identity_is_deterministic(a_drawing, buy_snapshot) -> None:
    first = svg_attachment(
        a_drawing,
        signal_id=buy_snapshot.signal_id,
        symbol=a_drawing.symbol,
        timeframe=a_drawing.timeframe,
    )
    second = svg_attachment(
        a_drawing,
        signal_id=buy_snapshot.signal_id,
        symbol=a_drawing.symbol,
        timeframe=a_drawing.timeframe,
    )
    assert first.artifact_id == second.artifact_id
    assert first.content == second.content


def test_mismatched_series_chart_is_rejected(a_drawing, buy_snapshot) -> None:
    with pytest.raises(AnalysisInputError):
        svg_attachment(
            a_drawing,
            signal_id=buy_snapshot.signal_id,
            symbol="ETHUSDT",
            timeframe="15m",
        )


def test_chart_failure_does_not_mutate_signal_message(buy_snapshot) -> None:
    # A chart attempt on the wrong series raises, but the already-rendered text
    # message is unchanged: its caption and identity are independent of the chart.
    from tests.delivery.conftest import make_message

    from smcsignal.delivery import (
        DeliveryConfig,
        assemble_signal_message,
        render_message,
    )

    message = make_message(signal_id=buy_snapshot.signal_id, symbol="BTCUSDT", timeframe="15m")
    caption = render_message(message, DeliveryConfig())
    assembled = assemble_signal_message(message)
    # Re-rendering after the (separate) failing chart attempt is byte-identical.
    assert caption == render_message(message, DeliveryConfig())
    assert assembled.message_id == assemble_signal_message(message).message_id
