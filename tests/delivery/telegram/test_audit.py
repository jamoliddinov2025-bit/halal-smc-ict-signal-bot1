"""Telegram audit-redaction tests (deterministic, secret-free)."""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.telegram.audit import (
    redact_chat_id,
    redact_token,
    telegram_audit_record,
)

from ._helpers import build_payload, chart_payload


def _make_record(payload, receipt, *, chat_id="-1001234567890"):
    return telegram_audit_record(payload, receipt, chat_id=chat_id)


def test_redact_chat_id_is_deterministic_and_lossy() -> None:
    assert redact_chat_id("-1001234567890") == redact_chat_id("-1001234567890")
    assert "-1001234567890" not in redact_chat_id("-1001234567890")
    assert redact_chat_id("abc") == "<redacted>"


def test_redact_token_never_leaks() -> None:
    assert redact_token("supersecrettoken") == "<token-redacted>"
    assert "supersecret" not in redact_token("supersecrettoken")
    with pytest.raises(AnalysisInputError):
        redact_token(None)


def test_audit_record_is_deterministic(buy_snapshot, telegram_sink) -> None:
    payload = build_payload(buy_snapshot)
    receipt = telegram_sink.deliver_payload(payload)
    a = _make_record(payload, receipt, chat_id="-1001234567890")
    b = _make_record(payload, receipt, chat_id="-1001234567890")
    assert a == b
    assert a.delivery_id == payload.delivery_id


def test_audit_record_redacts_full_chat_id(buy_snapshot, telegram_sink) -> None:
    payload = build_payload(buy_snapshot)
    # deliver through the fixture sink to obtain a real receipt
    receipt = telegram_sink.deliver_payload(payload)
    record = _make_record(payload, receipt, chat_id="-1009876543210")
    assert "-1009876543210" not in record.chat_redacted
    assert record.state == "DELIVERED"
    assert record.failure_category == "none"
    assert record.message_digest


def test_audit_record_captures_chart_artifact(buy_snapshot, a_drawing, telegram_sink) -> None:
    payload = chart_payload(buy_snapshot, a_drawing)
    receipt = telegram_sink.deliver_payload(payload)
    record = _make_record(payload, receipt, chat_id="-1001")
    assert record.chart_artifact_id is not None
    assert record.chart_artifact_id.startswith("chart:")


def test_audit_record_for_bad_types_rejected() -> None:
    with pytest.raises(AnalysisInputError):
        telegram_audit_record(object(), object())  # type: ignore[arg-type]
