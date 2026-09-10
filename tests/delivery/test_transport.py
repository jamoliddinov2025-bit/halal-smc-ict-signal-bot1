"""Phase 24D-B transport payload foundation tests (offline, additive).

These tests prove that the rendered payload produced by the frozen coordinator
is available to future transports as an immutable ``TransportPayload`` without
re-rendering and without changing any Phase 24B/24C behavior.
"""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery import (
    DeliveryCoordinator,
    DeliveryState,
    FakeTransportSink,
    NullSink,
    OfflinePayloadSink,
    TransportPayload,
    audit_record_for,
    delivery_identity,
    message_identity,
    transport_payload_from_outcome,
)


def _outcome_for(buy_snapshot, sink=None, drawing=None):
    sink = sink if sink is not None else FakeTransportSink()
    coordinator = DeliveryCoordinator(sink)
    return coordinator.deliver(buy_snapshot, "channel-buys", drawing=drawing)


# --- payload is available from a real coordinator outcome ----------------------


def test_payload_carries_metadata_and_rendered_text(buy_snapshot) -> None:
    outcome = _outcome_for(buy_snapshot)
    payload = transport_payload_from_outcome(outcome)
    assert isinstance(payload, TransportPayload)
    # delivery metadata identical to the outcome / deterministic ids
    assert payload.delivery_id == outcome.delivery_id
    assert payload.message_id == outcome.message_id
    assert payload.signal_id == buy_snapshot.signal_id
    assert payload.destination_id == "channel-buys"
    assert payload.attempt_number == outcome.attempt_number
    # rendered text matches the coordinator's rendered message (no re-render)
    assert payload.caption == outcome.caption
    assert payload.parts == outcome.parts
    assert payload.rendered.message_id == message_identity(buy_snapshot.signal_id)
    # deterministic delivery id relationship holds
    assert payload.delivery_id == delivery_identity(payload.message_id, "channel-buys")


def test_payload_chart_is_available_when_drawing_bound(buy_snapshot, a_drawing) -> None:
    outcome = _outcome_for(buy_snapshot, drawing=a_drawing)
    payload = transport_payload_from_outcome(outcome)
    assert payload.chart_present is True
    assert payload.chart is not None
    assert payload.chart.signal_id == buy_snapshot.signal_id
    assert payload.chart.drawing_id == a_drawing.drawing_id
    assert payload.chart.content == outcome.chart.content
    assert outcome.chart_artifact_id == payload.chart.artifact_id


def test_payload_without_chart(buy_snapshot) -> None:
    outcome = _outcome_for(buy_snapshot)  # no drawing
    payload = transport_payload_from_outcome(outcome)
    assert payload.chart_present is False
    assert payload.chart is None


def test_payload_is_deterministic_for_identical_input(buy_snapshot) -> None:
    a = transport_payload_from_outcome(_outcome_for(buy_snapshot))
    b = transport_payload_from_outcome(_outcome_for(buy_snapshot))
    assert a == b
    assert a.caption == b.caption
    assert a.delivery_id == b.delivery_id
    assert a.rendered == b.rendered


def test_payload_does_not_mutate_outcome_and_no_re_render(buy_snapshot) -> None:
    outcome = _outcome_for(buy_snapshot)
    signal_id = outcome.signal_id
    state = outcome.state
    caption = outcome.caption
    payload = transport_payload_from_outcome(outcome)
    # building a payload never changes the outcome or the signal
    assert outcome.signal_id == signal_id
    assert outcome.state is state
    assert outcome.caption == caption
    assert payload.caption == caption


# --- OfflinePayloadSink delivers the payload like a future transport -----------


def test_offline_payload_sink_receives_payload_and_reports(buy_snapshot) -> None:
    outcome = _outcome_for(buy_snapshot)
    payload = transport_payload_from_outcome(outcome)
    sink = OfflinePayloadSink()
    receipt = sink.deliver_payload(payload)
    assert len(sink.seen) == 1
    assert sink.seen[0] is payload
    assert receipt.state is DeliveryState.DELIVERED
    assert receipt.delivery_id == payload.delivery_id
    assert receipt.message_id == payload.message_id
    assert receipt.signal_id == payload.signal_id


def test_offline_payload_sink_records_text_and_chart(buy_snapshot, a_drawing) -> None:
    outcome = _outcome_for(buy_snapshot, drawing=a_drawing)
    payload = transport_payload_from_outcome(outcome)
    sink = OfflinePayloadSink()
    sink.deliver_payload(payload)
    seen = sink.seen[0]
    # a future transport can read text + chart from the recorded payload
    assert seen.caption == outcome.caption
    assert seen.chart_present is True
    assert seen.chart is not None
    assert seen.chart.mime_type == "image/svg+xml"


def test_offline_payload_sink_supports_state_outcomes(buy_snapshot) -> None:
    from smcsignal.delivery import FailureCategory

    outcome = _outcome_for(buy_snapshot)
    payload = transport_payload_from_outcome(outcome)
    sink = OfflinePayloadSink(
        outcomes=[DeliveryState.FAILED],
        failure_category=FailureCategory.TRANSPORT,
    )
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.FAILED
    assert receipt.failure_category is FailureCategory.TRANSPORT


def test_offline_payload_sink_is_offline(buy_snapshot) -> None:
    outcome = _outcome_for(buy_snapshot)
    payload = transport_payload_from_outcome(outcome)
    sink = OfflinePayloadSink([DeliveryState.UNKNOWN])
    receipt = sink.deliver_payload(payload)
    assert receipt.state is DeliveryState.UNKNOWN
    assert len(sink.seen) == 1


# --- payload from a disabled / no-op coordinator path ---------------------------


def test_payload_is_available_even_when_transport_is_noop(buy_snapshot) -> None:
    # NullSink returns NOT_ATTEMPTED but the coordinator still renders; the
    # payload (text/chart/metadata) remains available to a future transport.
    outcome = _outcome_for(buy_snapshot, sink=NullSink())
    assert outcome.state is DeliveryState.NOT_ATTEMPTED
    payload = transport_payload_from_outcome(outcome)
    assert payload.caption == outcome.caption
    assert payload.delivery_id == outcome.delivery_id


# --- validation / guards --------------------------------------------------------


def test_payload_from_outcome_rejects_non_outcome() -> None:
    with pytest.raises(AnalysisInputError):
        transport_payload_from_outcome(object())  # type: ignore[arg-type]


def test_payload_rejects_message_id_mismatch(buy_snapshot) -> None:
    outcome = _outcome_for(buy_snapshot)
    payload = transport_payload_from_outcome(outcome)
    # a tampered payload that contradicts its rendered message is rejected
    with pytest.raises(AnalysisInputError):
        TransportPayload(
            delivery_id=payload.delivery_id,
            message_id="message:different",
            signal_id=payload.signal_id,
            destination_id=payload.destination_id,
            attempt_number=1,
            rendered=payload.rendered,
        )


def test_payload_audit_relationship_unchanged(buy_snapshot) -> None:
    # The audit path over the outcome is untouched by the payload projection.
    outcome = _outcome_for(buy_snapshot)
    record = audit_record_for(outcome)
    assert record.delivery_id == outcome.delivery_id
    assert record.message_digest
    # and the payload still references the same signal/message as the audit
    payload = transport_payload_from_outcome(outcome)
    assert payload.signal_id == record.signal_id
    assert payload.message_id == record.message_id


def test_buy_and_only_buy_still_flows(buy_snapshot, non_buy_snapshot) -> None:
    # BUY flows; non-BUY is rejected before any sink/payload is produced.
    coordinator = DeliveryCoordinator(FakeTransportSink())
    outcome = coordinator.deliver(buy_snapshot, "channel-buys")
    assert outcome.state is DeliveryState.DELIVERED
    transport_payload_from_outcome(outcome)  # payload available
    with pytest.raises(AnalysisInputError):
        coordinator.deliver(non_buy_snapshot, "channel-buys")
