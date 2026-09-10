"""Shared offline helpers for the Telegram transport tests.

All helpers are deterministic and never perform a real network call; they only
simulate Telegram HTTP responses so the transport can be tested end-to-end.
"""

from __future__ import annotations

from typing import Any

from smcsignal.delivery import DeliveryCoordinator, FakeTransportSink
from smcsignal.delivery.telegram.http import HttpResponse
from smcsignal.delivery.transport import TransportPayload, transport_payload_from_outcome


class FakeHttpTransport:
    """A scripted, offline HTTP transport that never reaches the network.

    ``responses`` is a list of canned outcomes consumed in order (repeating the
    last once exhausted). Each item is either a ``(status, body)`` tuple, a
    ``TelegramTimeoutError`` / ``TelegramApiError`` to raise, or ``None`` which
    means a default ``200 {"ok": true}``.
    """

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses: list[Any] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        method = url.rstrip("/").rsplit("/", 1)[-1]
        self.calls.append({"method": method, "data": data, "url": url, "headers": dict(headers)})
        if self.responses:
            outcome = self.responses[0]
            if len(self.responses) > 1:
                self.responses.pop(0)
        else:
            outcome = None
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return HttpResponse(status=200, body='{"ok": true}')
        status, body = outcome
        return HttpResponse(status=int(status), body=body)


def ok_response() -> tuple[int, str]:
    """A successful Telegram response body."""
    return 200, '{"ok": true, "result": {"message_id": 1}}'


def build_payload(frame, drawing=None) -> TransportPayload:
    """Build an immutable TransportPayload from a real BUY snapshot."""
    coordinator = DeliveryCoordinator(FakeTransportSink())
    outcome = coordinator.deliver(frame, "channel-buys", drawing=drawing)
    assert outcome.state.value == "DELIVERED"
    return transport_payload_from_outcome(outcome)


def chart_payload(frame, drawing) -> TransportPayload:
    """Build a TransportPayload that includes a bound chart attachment."""
    return build_payload(frame, drawing=drawing)
