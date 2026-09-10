"""Telegram HTTP client tests (injectable transport, no real network)."""

from __future__ import annotations

import json

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.telegram.http import (
    HttpResponse,
    TelegramApiError,
    TelegramHttpClient,
    TelegramTimeoutError,
)

from ._helpers import FakeHttpTransport


def _client(fake: FakeHttpTransport, *, responses=None):
    if responses is not None:
        fake.responses = list(responses)
    return TelegramHttpClient("TEST:TOKEN123", transport=fake, timeout=5.0)


def test_send_message_posts_json_with_parse_mode(fake_transport) -> None:
    client = _client(fake_transport, responses=[(200, '{"ok": true}')])
    response = client.send_message("-100123", "hello", parse_mode="HTML")
    assert response.status == 200
    call = fake_transport.calls[0]
    assert call["method"] == "sendMessage"
    assert "TEST:TOKEN123" in call["url"]  # token used in request url only
    payload = json.loads(call["data"].decode("utf-8"))
    assert payload["chat_id"] == "-100123"
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "HTML"


def test_send_document_multipart_contains_filename(fake_transport) -> None:
    client = _client(fake_transport, responses=[(200, '{"ok": true}')])
    response = client.send_document("-100123", "drawing.svg", b"<svg></svg>", caption="caption")
    assert response.status == 200
    call = fake_transport.calls[0]
    assert call["method"] == "sendDocument"
    body = call["data"].decode("utf-8", errors="replace")
    assert 'name="chat_id"' in body
    assert 'name="document"; filename="drawing.svg"' in body
    assert "multipart/form-data" in call["headers"]["Content-Type"]


def test_http_error_raises_api_error(fake_transport) -> None:
    client = _client(fake_transport, responses=[])
    # force an api error via exception object path by raising from transport
    fake_transport.responses.append(TelegramApiError(500, "boom"))
    with pytest.raises(TelegramApiError):
        client.send_message("-100123", "x")


def test_timeout_propagates(fake_transport) -> None:
    client = _client(fake_transport, responses=[TelegramTimeoutError("timeout")])
    with pytest.raises(TelegramTimeoutError):
        client.send_message("-100123", "x")


def test_client_rejects_empty_token(fake_transport) -> None:
    with pytest.raises(AnalysisInputError):
        TelegramHttpClient("  ", transport=fake_transport)


def test_client_returns_http_response(fake_transport) -> None:
    client = _client(fake_transport, responses=[(200, '{"ok": true}')])
    response = client.send_message("-100123", "hi")
    assert isinstance(response, HttpResponse)
    assert response.status == 200
    assert response.body == '{"ok": true}'
