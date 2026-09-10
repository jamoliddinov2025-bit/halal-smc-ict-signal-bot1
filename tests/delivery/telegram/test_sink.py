"""TelegramSink offline tests (mocked HTTP, no real Telegram calls)."""

from __future__ import annotations

import dataclasses

from smcsignal.delivery import DeliveryState, FailureCategory
from smcsignal.delivery.telegram import (
    TelegramConfig,
    TelegramHttpClient,
    TelegramSink,
    TelegramTimeoutError,
)

from ._helpers import FakeHttpTransport, build_payload, chart_payload, ok_response


def _sink(fake: FakeHttpTransport, *, responses=None, config=None, destinations=None):
    # when explicit responses supplied, wire them onto the fake transport
    if responses is not None:
        fake.responses = list(responses)
    client = TelegramHttpClient("TEST:TOKEN123", transport=fake, timeout=5.0)
    cfg = config if config is not None else TelegramConfig(enabled=True, retry_max_attempts=1)
    return TelegramSink(
        "TEST:TOKEN123",
        config=cfg,
        destinations=destinations if destinations is not None else {"channel-buys": "-100123"},
        http_client=client,
    )


def test_success_delivered(buy_snapshot, fake_transport):
    sink = _sink(fake_transport, responses=[ok_response()])
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    assert receipt.failure_category is FailureCategory.NONE
    assert receipt.delivery_id == payload.delivery_id
    assert fake_transport.calls[0]["method"] == "sendMessage"


def test_accepted_but_unconfirmed_is_sent(buy_snapshot, fake_transport):
    # HTTP 200 but body without ok:true -> SENT
    sink = _sink(fake_transport, responses=[(200, "{}")])
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.SENT
    assert receipt.failure_category is FailureCategory.NONE


def test_timeout_is_unknown(buy_snapshot, fake_transport):
    sink = _sink(fake_transport, responses=[TelegramTimeoutError("timed out")])
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.UNKNOWN
    assert receipt.failure_category is FailureCategory.TIMEOUT


def test_rate_limit_429_failed_rate_limit(buy_snapshot, fake_transport):
    # single attempt -> 429 not retried within 1 attempt -> FAILED RATE_LIMIT
    cfg = TelegramConfig(enabled=True, retry_max_attempts=1)
    sink = _sink(
        fake_transport, config=cfg, responses=[(429, '{"ok":false,"description":"flood"}')]
    )
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.FAILED
    assert receipt.failure_category is FailureCategory.RATE_LIMIT


def test_rate_limit_retried_then_success(buy_snapshot, fake_transport):
    cfg = TelegramConfig(enabled=True, retry_max_attempts=3, retry_backoff_seconds=0.0)
    sink = _sink(
        fake_transport,
        config=cfg,
        responses=[(429, '{"ok":false}'), ok_response()],
    )
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    assert len(fake_transport.calls) == 2


def test_invalid_token_401_failed_config(buy_snapshot, fake_transport):
    sink = _sink(fake_transport, responses=[(401, '{"ok":false,"description":"Unauthorized"}')])
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.FAILED
    assert receipt.failure_category is FailureCategory.CONFIG


def test_missing_token_disables_transport(buy_snapshot):
    sink = TelegramSink(None, config=TelegramConfig(enabled=True))
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert sink.enabled is False
    assert receipt.state is DeliveryState.NOT_ATTEMPTED
    assert receipt.failure_category is FailureCategory.CONFIG


def test_disabled_config_noop(buy_snapshot, fake_transport):
    sink = _sink(fake_transport, config=TelegramConfig(enabled=False))
    payload = build_payload(buy_snapshot)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.NOT_ATTEMPTED
    assert fake_transport.calls == []


def test_unknown_destination_not_attempted(buy_snapshot, fake_transport):
    sink = _sink(fake_transport)
    payload = build_payload(buy_snapshot)
    other = dataclasses.replace(payload, destination_id="unknown-channel")
    receipt = sink.deliver_payload(other)
    assert receipt.state is DeliveryState.NOT_ATTEMPTED
    assert fake_transport.calls == []  # no network call for bad destination


def test_chart_delivered_as_document(buy_snapshot, a_drawing, fake_transport):
    # two HTTP calls: sendMessage then sendDocument
    responses = [ok_response(), ok_response()]
    sink = _sink(fake_transport, responses=responses)
    payload = chart_payload(buy_snapshot, a_drawing)
    assert payload.chart_present is True
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    methods = [c["method"] for c in fake_transport.calls]
    assert methods == ["sendMessage", "sendDocument"]
    assert sink.last_chart_failure is None


def test_chart_failure_does_not_invalidate_text(buy_snapshot, a_drawing, fake_transport):
    # text succeeds, chart document fails -> text still DELIVERED
    responses = [ok_response(), (500, '{"ok":false}')]
    sink = _sink(fake_transport, responses=responses)
    payload = chart_payload(buy_snapshot, a_drawing)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED  # text not invalidated
    assert sink.last_chart_failure is not None
    methods = [c["method"] for c in fake_transport.calls]
    assert "sendMessage" in methods and "sendDocument" in methods


def test_chart_disabled_sends_text_only(buy_snapshot, a_drawing, fake_transport):
    cfg = TelegramConfig(enabled=True, chart_enabled=False)
    sink = _sink(fake_transport, config=cfg, responses=[ok_response()])
    payload = chart_payload(buy_snapshot, a_drawing)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    methods = [c["method"] for c in fake_transport.calls]
    assert methods == ["sendMessage"]


def test_caption_over_budget_is_refused_without_network(buy_snapshot, fake_transport):
    """D1: an over-budget caption is never sent (no truncation, no API call)."""
    cfg = TelegramConfig(enabled=True, retry_max_attempts=1, max_message_chars=10)
    sink = _sink(fake_transport, config=cfg, responses=[ok_response()])
    payload = build_payload(buy_snapshot)
    assert len(payload.caption) > 10  # the real payload is well over the budget
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.NOT_ATTEMPTED
    assert receipt.failure_category is FailureCategory.CONFIG
    assert fake_transport.calls == []  # guard runs before any network call
    # content is read only: never truncated, never re-rendered
    assert len(payload.caption) > 10


def test_caption_exactly_at_budget_is_delivered(buy_snapshot, fake_transport):
    """D1 boundary: the budget is inclusive (a caption of exactly N chars sends)."""
    payload = build_payload(buy_snapshot)
    cfg = TelegramConfig(enabled=True, retry_max_attempts=1, max_message_chars=len(payload.caption))
    sink = _sink(fake_transport, config=cfg, responses=[ok_response()])
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    assert [c["method"] for c in fake_transport.calls] == ["sendMessage"]


def test_default_budget_never_refuses_a_real_caption(buy_snapshot, fake_transport):
    """D1: the default guard is unreachable for real payloads (4000 < 4096)."""
    payload = build_payload(buy_snapshot)
    assert len(payload.caption) <= TelegramConfig().max_message_chars
    sink = _sink(fake_transport, responses=[ok_response()])
    assert sink.deliver_payload(payload).state is DeliveryState.DELIVERED


def test_over_budget_chart_payload_leaves_chart_unsent(buy_snapshot, a_drawing, fake_transport):
    """D1: the guard short-circuits before text and chart alike."""
    cfg = TelegramConfig(enabled=True, retry_max_attempts=1, max_message_chars=10)
    sink = _sink(fake_transport, config=cfg, responses=[ok_response(), ok_response()])
    payload = chart_payload(buy_snapshot, a_drawing)
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.NOT_ATTEMPTED
    assert fake_transport.calls == []
    assert sink.last_chart_failure is None


def test_text_only_payload_no_document_call(buy_snapshot, fake_transport):
    sink = _sink(fake_transport, responses=[ok_response()])
    payload = build_payload(buy_snapshot)  # no chart
    assert payload.chart_present is False
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.DELIVERED
    methods = [c["method"] for c in fake_transport.calls]
    assert methods == ["sendMessage"]
