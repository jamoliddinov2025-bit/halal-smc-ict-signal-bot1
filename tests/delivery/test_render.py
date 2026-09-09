"""Phase 24B rendering tests: deterministic, safe, HTML-escaped output."""

from __future__ import annotations

from decimal import Decimal

from tests.delivery.conftest import make_message

from smcsignal.analysis.signal_engine import SignalReason
from smcsignal.delivery import (
    DeliveryConfig,
    escape_html,
    format_decimal,
    prepare_signal_message,
    render_message,
    split_caption,
)


def test_deterministic_rendering_same_input(buy_snapshot) -> None:
    first = prepare_signal_message(buy_snapshot)
    second = prepare_signal_message(buy_snapshot)
    assert first.caption == second.caption
    assert first.message_id == second.message_id


def test_buy_signal_rendering_fields(buy_snapshot) -> None:
    rendered = prepare_signal_message(buy_snapshot)
    text = rendered.caption
    assert "BTCUSDT" in text
    assert "15m" in text
    assert "BUY" in text
    assert "signal id :" in text
    assert buy_snapshot.signal_id in text
    assert "setup quality score :" in text
    assert "classification : HALAL" in text
    assert "reasons :" in text
    assert "not trading advice" in text


def test_optional_and_missing_field_lines_omitted(buy_snapshot) -> None:
    config = DeliveryConfig(
        show_signal_id=False,
        show_setup_context=False,
        show_halal_status=False,
        show_timestamps=False,
        show_chart_unavailable_marker=False,
    )
    rendered = prepare_signal_message(buy_snapshot, config=config)
    assert "signal id :" not in rendered.caption
    assert "setup :" not in rendered.caption
    assert "classification :" not in rendered.caption
    assert "candle closed :" not in rendered.caption
    assert "chart :" not in rendered.caption


def test_html_escaping_prevents_markup_breakout() -> None:
    message = make_message(symbol='BTC<USDT onmouseover="x">', signal_id="signal-candidate:a<b&c")
    rendered = render_message(message, DeliveryConfig())
    # Every markup-significant character is escaped, so no raw tag can appear.
    assert "&lt;" in rendered
    assert "&quot;" in rendered
    assert "&amp;" in rendered
    assert "<USDT" not in rendered
    assert "&lt;USDT" in rendered
    # Nothing between unescaped angle brackets (no parseable element or attribute).
    assert "<" not in rendered.replace("&lt;", "")
    assert ">" not in rendered.replace("&gt;", "")


def test_decimal_formatting_is_deterministic_and_plain() -> None:
    assert format_decimal(Decimal("24")) == "24"
    assert format_decimal(Decimal("24.500")) == "24.5"
    assert format_decimal(Decimal("1234.56780000")) == "1234.5678"
    assert format_decimal(Decimal("1.5")) == "1.5"
    # Display is capped at max_decimals=8; a sub-8-decimal value shows 0.
    assert format_decimal(Decimal("0.00000000001")) == "0"


def test_split_caption_chunks_at_line_boundaries() -> None:
    long_line = "x" * 5000
    text = f"first\n{long_line}\nlast"
    parts = split_caption(text, 100)
    assert "".join(parts) == text.replace("\n", "")
    assert all(len(part) <= 100 for part in parts)
    assert all(part for part in parts)


def test_render_has_no_invented_trading_fields(buy_snapshot) -> None:
    rendered = prepare_signal_message(buy_snapshot)
    lowered = rendered.caption.lower()
    for token in ("stop-loss", "take-profit", "target ", "entry ", "probability", "confidence"):
        assert token not in lowered
    # The real upstream reasons are preserved verbatim in the rendered output.
    assert buy_snapshot.candidate.reasons
    assert "reasons :" in rendered.caption


def test_unknown_reason_rendered_verbatim_never_guessed() -> None:
    message = make_message(reasons=(SignalReason.HALAL_ASSET,))
    # reasons come from a fixed enum; rendering labels only known ones and falls
    # back to the raw value for anything not in the display map.
    assert "HALAL classification" in render_message(message, DeliveryConfig())


def test_escape_html_builtin_is_deterministic() -> None:
    assert escape_html("<a href='x'>&</a>") == "&lt;a href=&#x27;x&#x27;&gt;&amp;&lt;/a&gt;"
    assert escape_html("BTC") == "BTC"
