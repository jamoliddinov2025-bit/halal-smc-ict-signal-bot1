"""Phase 24D-D integration tests: orchestration → Telegram transport.

Every test uses fake transports (``OfflinePayloadSink`` or a real ``TelegramSink``
wired to the scripted offline HTTP transport). No test performs a real network
call, and no test requires a real token.
"""

from __future__ import annotations

import dataclasses

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery import DeliveryState, FailureCategory
from smcsignal.delivery.models import DeliveryAttempt, DeliveryReceipt
from smcsignal.delivery.orchestrator import DeliveryCoordinator, OrchestrationConfig
from smcsignal.delivery.sink import DeliveryRegistry
from smcsignal.delivery.telegram import (
    TelegramConfig,
    TelegramDeliveryCounters,
    TelegramDeliveryIntegration,
    TelegramDeliveryResult,
    TelegramHttpClient,
    TelegramPayloadBridge,
    TelegramSink,
    TelegramTimeoutError,
)
from smcsignal.delivery.transport import OfflinePayloadSink, TransportPayload

from ._helpers import FakeHttpTransport, ok_response


def _telegram_sink(fake: FakeHttpTransport, *, responses=None) -> TelegramSink:
    if responses is not None:
        fake.responses = list(responses)
    return TelegramSink(
        "TEST:TOKEN123",
        config=TelegramConfig(enabled=True, retry_max_attempts=1),
        destinations={"channel-buys": "-1001234567890"},
        http_client=TelegramHttpClient("TEST:TOKEN123", transport=fake, timeout=5.0),
    )


# --- happy path: outcome → payload → transport ------------------------------


def test_deliver_projects_outcome_to_payload_and_sends(buy_snapshot):
    transport = OfflinePayloadSink(outcomes=[DeliveryState.DELIVERED])
    integration = TelegramDeliveryIntegration(transport)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert isinstance(result, TelegramDeliveryResult)
    assert result.state is DeliveryState.DELIVERED
    assert result.failure_category is FailureCategory.NONE
    assert result.was_sent is True
    assert len(transport.seen) == 1
    payload = transport.seen[0]
    assert isinstance(payload, TransportPayload)
    # the transport receives exactly the frozen rendered content
    assert payload.caption == result.outcome.caption
    assert payload.delivery_id == result.outcome.delivery_id
    assert payload.message_id == result.outcome.message_id
    assert payload.signal_id == result.outcome.signal_id
    assert payload.destination_id == "channel-buys"


def test_deliver_uses_rendered_content_without_rerendering(buy_snapshot):
    """The transport payload is a projection, never a second render."""
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.payload is not None
    assert result.payload.rendered is result.outcome.rendered  # same object
    assert result.payload.parts == result.outcome.parts


def test_deliver_through_real_telegram_sink_with_fake_http(buy_snapshot, fake_transport):
    """The real 24D-C sink drives the production path with a scripted HTTP stub."""
    sink = _telegram_sink(fake_transport, responses=[ok_response()])
    integration = TelegramDeliveryIntegration(sink)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.DELIVERED
    assert [call["method"] for call in fake_transport.calls] == ["sendMessage"]


def test_deliver_sends_exactly_once_per_call(buy_snapshot):
    """Projection mode must not cause a second send via the coordinator."""
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)

    integration.deliver(buy_snapshot, "channel-buys")

    assert len(transport.seen) == 1


def test_deliver_outcome_accepts_an_existing_outcome(buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)
    outcome = DeliveryCoordinator(None).deliver(buy_snapshot, "channel-buys")

    result = integration.deliver_outcome(outcome)

    assert result.state is DeliveryState.DELIVERED
    assert result.outcome is outcome
    assert len(transport.seen) == 1


# --- deterministic ids and dedup --------------------------------------------


def test_delivery_id_is_deterministic_across_integrations(buy_snapshot):
    first = TelegramDeliveryIntegration(OfflinePayloadSink())
    second = TelegramDeliveryIntegration(OfflinePayloadSink())

    assert (
        first.deliver(buy_snapshot, "channel-buys").delivery_id
        == second.deliver(buy_snapshot, "channel-buys").delivery_id
    )


def test_repeat_delivery_is_skipped_as_duplicate(buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)

    first = integration.deliver(buy_snapshot, "channel-buys")
    second = integration.deliver(buy_snapshot, "channel-buys")

    assert first.state is DeliveryState.DELIVERED
    assert second.state is DeliveryState.SKIPPED_DUPLICATE
    assert second.failure_category is FailureCategory.NONE
    assert second.was_sent is False
    assert second.payload is None
    assert len(transport.seen) == 1  # nothing re-sent
    assert second.delivery_id == first.delivery_id


def test_shared_registry_deduplicates_across_integrations(buy_snapshot):
    registry = DeliveryRegistry()
    transport = OfflinePayloadSink()
    first = TelegramDeliveryIntegration(transport, registry=registry)
    second = TelegramDeliveryIntegration(transport, registry=registry)

    assert first.deliver(buy_snapshot, "channel-buys").state is DeliveryState.DELIVERED
    assert second.deliver(buy_snapshot, "channel-buys").state is DeliveryState.SKIPPED_DUPLICATE
    assert len(transport.seen) == 1


def test_distinct_destinations_are_independent(buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)

    first = integration.deliver(buy_snapshot, "channel-buys")
    second = integration.deliver(buy_snapshot, "other-channel")

    assert first.state is DeliveryState.DELIVERED
    assert second.state is DeliveryState.DELIVERED
    assert first.delivery_id != second.delivery_id  # destination is part of identity
    assert len(transport.seen) == 2


def test_dedup_disabled_by_orchestration_config(buy_snapshot):
    transport = OfflinePayloadSink()
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(deduplicate=False))
    integration = TelegramDeliveryIntegration(transport, coordinator=coordinator)

    integration.deliver(buy_snapshot, "channel-buys")
    second = integration.deliver(buy_snapshot, "channel-buys")

    assert second.state is DeliveryState.DELIVERED
    assert len(transport.seen) == 2  # delivered twice on request


# --- retry semantics --------------------------------------------------------


def test_failed_delivery_retries_up_to_the_cap(buy_snapshot):
    transport = OfflinePayloadSink(
        outcomes=[DeliveryState.FAILED, DeliveryState.DELIVERED],
        failure_category=FailureCategory.TRANSPORT,
    )
    integration = TelegramDeliveryIntegration(transport, max_attempts=2)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.DELIVERED
    assert result.attempt_number == 2
    assert len(transport.seen) == 2


def test_exhausted_retries_report_failed(buy_snapshot):
    transport = OfflinePayloadSink(
        outcomes=[DeliveryState.FAILED],
        failure_category=FailureCategory.TRANSPORT,
    )
    integration = TelegramDeliveryIntegration(transport, max_attempts=3)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.FAILED
    assert result.failure_category is FailureCategory.TRANSPORT
    assert len(transport.seen) == 3  # the cap is honoured, no infinite retry


def test_unknown_is_never_retried(buy_snapshot):
    """A timeout may already have delivered; retrying would risk duplicates."""
    fake = FakeHttpTransport()
    sink = _telegram_sink(fake, responses=[TelegramTimeoutError("timed out")])
    integration = TelegramDeliveryIntegration(sink, max_attempts=3)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.UNKNOWN
    assert result.failure_category is FailureCategory.TIMEOUT
    assert [c["method"] for c in fake.calls] == ["sendMessage"]  # exactly one


def test_not_attempted_is_never_retried(buy_snapshot):
    transport = OfflinePayloadSink(outcomes=[DeliveryState.NOT_ATTEMPTED])
    integration = TelegramDeliveryIntegration(transport, max_attempts=3)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.NOT_ATTEMPTED
    assert len(transport.seen) == 1


def test_retry_attempt_number_is_visible_to_the_transport(buy_snapshot):
    transport = OfflinePayloadSink(
        outcomes=[DeliveryState.FAILED, DeliveryState.DELIVERED],
        failure_category=FailureCategory.TRANSPORT,
    )
    integration = TelegramDeliveryIntegration(transport, max_attempts=2)

    integration.deliver(buy_snapshot, "channel-buys")

    assert [payload.attempt_number for payload in transport.seen] == [1, 2]


def test_transport_level_retry_composes_with_driver_retry(buy_snapshot, fake_transport):
    """Both scopes are bounded: transport 429 retry inside, driver cap outside."""
    fake_transport.responses = [(429, '{"ok":false}'), ok_response()]
    sink = TelegramSink(
        "TEST:TOKEN123",
        config=TelegramConfig(enabled=True, retry_max_attempts=2, retry_backoff_seconds=0.0),
        destinations={"channel-buys": "-1001234567890"},
        http_client=TelegramHttpClient("TEST:TOKEN123", transport=fake_transport, timeout=5.0),
        sleep_fn=lambda _seconds: None,
    )
    integration = TelegramDeliveryIntegration(sink, max_attempts=3)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.DELIVERED
    assert [c["method"] for c in fake_transport.calls] == ["sendMessage", "sendMessage"]


# --- disabled / not-attempted paths -----------------------------------------


def test_disabled_orchestration_master_switch_sends_nothing(buy_snapshot):
    transport = OfflinePayloadSink()
    coordinator = DeliveryCoordinator(None, config=OrchestrationConfig(enabled=False))
    integration = TelegramDeliveryIntegration(transport, coordinator=coordinator)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.NOT_ATTEMPTED
    assert result.failure_category is FailureCategory.CONFIG
    assert result.was_sent is False
    assert transport.seen == []


def test_disabled_transport_reports_not_attempted_and_is_not_recorded(buy_snapshot):
    """A disabled 24D-C transport sends nothing and does not mark the registry."""
    registry = DeliveryRegistry()
    sink = TelegramSink(None, config=TelegramConfig(enabled=True))  # no token
    integration = TelegramDeliveryIntegration(sink, registry=registry)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.state is DeliveryState.NOT_ATTEMPTED
    assert result.failure_category is FailureCategory.CONFIG
    assert result.was_sent is False
    assert registry.is_delivered(result.delivery_id) is False


def test_unresolvable_destination_fails_without_network(buy_snapshot, fake_transport):
    sink = _telegram_sink(fake_transport, responses=[ok_response()])
    integration = TelegramDeliveryIntegration(sink)

    result = integration.deliver(buy_snapshot, "not-configured")

    assert result.state is DeliveryState.NOT_ATTEMPTED
    assert result.failure_category is FailureCategory.CONFIG
    assert fake_transport.calls == []


# --- chart path -------------------------------------------------------------


def test_chart_payload_is_sent_after_text(buy_snapshot, a_drawing, fake_transport):
    sink = _telegram_sink(fake_transport, responses=[ok_response(), ok_response()])
    integration = TelegramDeliveryIntegration(sink)

    result = integration.deliver(buy_snapshot, "channel-buys", drawing=a_drawing)

    assert result.state is DeliveryState.DELIVERED
    assert result.payload is not None and result.payload.chart_present is True
    assert [c["method"] for c in fake_transport.calls] == ["sendMessage", "sendDocument"]


def test_chart_failure_never_invalidates_text_through_integration(
    buy_snapshot, a_drawing, fake_transport
):
    sink = _telegram_sink(fake_transport, responses=[ok_response(), (500, '{"ok":false}')])
    integration = TelegramDeliveryIntegration(sink)

    result = integration.deliver(buy_snapshot, "channel-buys", drawing=a_drawing)

    assert result.state is DeliveryState.DELIVERED
    assert result.failure_category is FailureCategory.NONE
    assert sink.last_chart_failure is not None


# --- audit ------------------------------------------------------------------


def test_audit_record_is_deterministic_and_redacted(buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)

    result = integration.deliver(buy_snapshot, "channel-buys")
    first = result.audit_record(chat_id="-1001234567890")
    second = result.audit_record(chat_id="-1001234567890")

    assert first == second
    assert "-1001234567890" not in first.chat_redacted
    assert first.state == "DELIVERED"
    assert first.failure_category == "none"
    assert first.message_id == result.outcome.message_id
    assert first.message_digest


def test_audit_record_absent_for_an_unsent_delivery(buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)
    integration.deliver(buy_snapshot, "channel-buys")
    duplicate = integration.deliver(buy_snapshot, "channel-buys")

    with pytest.raises(AnalysisInputError):
        duplicate.audit_record()


# --- counters ---------------------------------------------------------------


def test_counters_track_states_per_destination(buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)

    integration.deliver(buy_snapshot, "channel-buys")
    integration.deliver(buy_snapshot, "channel-buys")  # duplicate

    counters = integration.counters("channel-buys")
    assert counters.delivered == 1
    assert counters.skipped_duplicate == 1
    assert counters.delivered_total == 1
    assert counters.total == 2


def test_counters_are_zero_for_an_unseen_destination(buy_snapshot):
    integration = TelegramDeliveryIntegration(OfflinePayloadSink())

    assert integration.counters("never-used") == TelegramDeliveryCounters()
    assert integration.counters("never-used").total == 0


def test_counter_summary_is_sorted_and_read_only(buy_snapshot):
    integration = TelegramDeliveryIntegration(OfflinePayloadSink())
    integration.deliver(buy_snapshot, "zeta")
    integration.deliver(buy_snapshot, "alpha")

    summary = integration.counter_summary
    assert list(summary) == ["alpha", "zeta"]
    summary["mutated"] = TelegramDeliveryCounters()  # a copy, not internal state
    assert "mutated" not in integration.counter_summary


def test_counters_do_not_alter_delivery_outcomes(buy_snapshot):
    """Bookkeeping is inert: the same inputs give the same receipts."""
    plain = TelegramDeliveryIntegration(OfflinePayloadSink()).deliver(buy_snapshot, "channel-buys")
    counted = TelegramDeliveryIntegration(OfflinePayloadSink())
    counted.deliver(buy_snapshot, "ghost")
    result = counted.deliver(buy_snapshot, "channel-buys")

    assert result.state is plain.state
    assert result.delivery_id == plain.delivery_id
    assert result.attempt_number == plain.attempt_number


# --- the bridge -------------------------------------------------------------


def test_bridge_requires_a_sink():
    with pytest.raises(AnalysisInputError):
        TelegramPayloadBridge(None)  # type: ignore[arg-type]


def test_bridge_refuses_to_send_without_a_bound_payload(buy_snapshot):
    bridge = TelegramPayloadBridge(OfflinePayloadSink())
    outcome = DeliveryCoordinator(None).deliver(buy_snapshot, "channel-buys")
    attempt = DeliveryAttempt(
        delivery_id=outcome.delivery_id,
        message_id=outcome.message_id,
        signal_id=outcome.signal_id,
        destination_id=outcome.destination_id,
        attempt_number=1,
    )

    with pytest.raises(AnalysisInputError):
        bridge.deliver(attempt)


def test_bridge_rejects_an_identity_mismatch(buy_snapshot):
    transport = OfflinePayloadSink()
    bridge = TelegramPayloadBridge(transport)
    outcome = DeliveryCoordinator(None).deliver(buy_snapshot, "channel-buys")
    from smcsignal.delivery.transport import transport_payload_from_outcome

    bridge.bind_payload(transport_payload_from_outcome(outcome))
    mismatched = DeliveryAttempt(
        delivery_id=outcome.delivery_id,
        message_id=outcome.message_id,
        signal_id=outcome.signal_id,
        destination_id="some-other-destination",
        attempt_number=1,
    )

    with pytest.raises(AnalysisInputError):
        bridge.deliver(mismatched)
    assert transport.seen == []


def test_bridge_releases_the_payload_after_delivery(buy_snapshot):
    integration = TelegramDeliveryIntegration(OfflinePayloadSink())

    integration.deliver(buy_snapshot, "channel-buys")

    assert integration.bridge.bound_payload is None


def test_bridge_rejects_a_non_payload_binding():
    bridge = TelegramPayloadBridge(OfflinePayloadSink())

    with pytest.raises(AnalysisInputError):
        bridge.bind_payload("not-a-payload")  # type: ignore[arg-type]


# --- validation and immutability --------------------------------------------


def test_integration_rejects_a_bad_attempt_cap():
    for bad in (0, -1, 1.5, True):
        with pytest.raises(AnalysisInputError):
            TelegramDeliveryIntegration(OfflinePayloadSink(), max_attempts=bad)  # type: ignore[arg-type]


def test_deliver_outcome_rejects_a_non_outcome():
    integration = TelegramDeliveryIntegration(OfflinePayloadSink())

    with pytest.raises(AnalysisInputError):
        integration.deliver_outcome("not-an-outcome")  # type: ignore[arg-type]


def test_non_buy_signal_is_rejected_before_any_send(non_buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)

    with pytest.raises(AnalysisInputError):
        integration.deliver(non_buy_snapshot, "channel-buys")
    assert transport.seen == []


def test_delivery_does_not_mutate_the_signal_or_the_rendered_message(buy_snapshot):
    transport = OfflinePayloadSink()
    integration = TelegramDeliveryIntegration(transport)
    frame_before = dataclasses.asdict(buy_snapshot)

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert dataclasses.asdict(buy_snapshot) == frame_before
    assert result.outcome is not None
    assert result.outcome.rendered.caption == result.payload.caption  # unchanged content


def test_result_exposes_the_frozen_receipt_fields(buy_snapshot):
    integration = TelegramDeliveryIntegration(OfflinePayloadSink())

    result = integration.deliver(buy_snapshot, "channel-buys")

    assert result.delivery_id == result.receipt.delivery_id
    assert result.signal_id == result.receipt.signal_id
    assert result.destination_id == "channel-buys"
    assert result.delivered is True
    assert isinstance(result.receipt, DeliveryReceipt)
