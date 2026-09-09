"""Phase 24B delivery-abstraction tests: sink, dedup, retry, states."""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery import (
    DeliveryRegistry,
    DeliveryState,
    FailureCategory,
    FakeTransportSink,
    NullSink,
    deliver_message,
    deliver_signal,
    delivery_identity,
    message_identity,
    prepare_signal_message,
)


def _rendered(buy_snapshot):
    return prepare_signal_message(buy_snapshot)


def test_successful_delivery(buy_snapshot) -> None:
    sink = FakeTransportSink([DeliveryState.DELIVERED])
    receipt = deliver_message(_rendered(buy_snapshot), "channel-buys", sink)
    assert receipt.state is DeliveryState.DELIVERED
    assert receipt.failure_category is FailureCategory.NONE
    assert len(sink.seen) == 1
    assert sink.seen[0].destination_id == "channel-buys"


def test_delivery_identity_stable_across_repeated_calls(buy_snapshot) -> None:
    rendered = _rendered(buy_snapshot)
    first = deliver_message(rendered, "channel-buys", FakeTransportSink())
    second = deliver_message(rendered, "channel-buys", FakeTransportSink())
    expected = delivery_identity(rendered.message_id, "channel-buys")
    assert first.delivery_id == second.delivery_id == expected


def test_no_sink_means_not_attempted(buy_snapshot) -> None:
    receipt = deliver_message(_rendered(buy_snapshot), "channel-buys", None)
    assert receipt.state is DeliveryState.NOT_ATTEMPTED


def test_null_sink_is_offline_noop(buy_snapshot) -> None:
    sink = NullSink()
    receipt = deliver_message(_rendered(buy_snapshot), "channel-buys", sink)
    assert receipt.state is DeliveryState.NOT_ATTEMPTED
    assert len(sink.seen) == 1


def test_failure_is_retried_to_success(buy_snapshot) -> None:
    sink = FakeTransportSink(
        [DeliveryState.FAILED, DeliveryState.DELIVERED],
        failure_category=FailureCategory.TRANSPORT,
    )
    receipt = deliver_message(_rendered(buy_snapshot), "channel-buys", sink, max_attempts=3)
    assert receipt.state is DeliveryState.DELIVERED
    assert len(sink.seen) == 2


def test_exhausted_failures_reach_failed_state(buy_snapshot) -> None:
    sink = FakeTransportSink(
        [DeliveryState.FAILED],
        failure_category=FailureCategory.NETWORK,
    )
    receipt = deliver_message(_rendered(buy_snapshot), "channel-buys", sink, max_attempts=3)
    assert receipt.state is DeliveryState.FAILED
    assert len(sink.seen) == 3


def test_unknown_outcome_is_not_retried(buy_snapshot) -> None:
    sink = FakeTransportSink(
        [DeliveryState.UNKNOWN],
        failure_category=FailureCategory.TIMEOUT,
    )
    receipt = deliver_message(_rendered(buy_snapshot), "channel-buys", sink, max_attempts=5)
    assert receipt.state is DeliveryState.UNKNOWN
    assert len(sink.seen) == 1  # ambiguous result -> stop, do not hammer


def test_registry_skips_duplicate_after_delivered(buy_snapshot) -> None:
    registry = DeliveryRegistry()
    rendered = _rendered(buy_snapshot)
    first = deliver_message(rendered, "channel-buys", FakeTransportSink(), registry=registry)
    assert first.state is DeliveryState.DELIVERED
    second = deliver_message(rendered, "channel-buys", FakeTransportSink(), registry=registry)
    assert second.state is DeliveryState.SKIPPED_DUPLICATE


def test_deliver_signal_rejects_non_buy(non_buy_snapshot) -> None:
    with pytest.raises(AnalysisInputError):
        deliver_signal(non_buy_snapshot, "channel-buys", FakeTransportSink())


def test_deliver_signal_success(buy_snapshot) -> None:
    receipt = deliver_signal(buy_snapshot, "channel-buys", FakeTransportSink())
    assert receipt.state is DeliveryState.DELIVERED


def test_deliver_signal_with_attached_chart(buy_snapshot, a_drawing) -> None:
    sink = FakeTransportSink()
    receipt = deliver_signal(
        buy_snapshot,
        "channel-buys",
        sink,
        drawing=a_drawing,
    )
    assert receipt.state is DeliveryState.DELIVERED
    attempt = sink.seen[0]
    assert attempt.chart_artifact_id is not None
    assert attempt.chart_artifact_id.startswith("chart:")


def test_retry_uses_same_delivery_id(buy_snapshot) -> None:
    rendered = _rendered(buy_snapshot)
    delivery = delivery_identity(rendered.message_id, "channel-buys")
    assert delivery == delivery_identity(rendered.message_id, "channel-buys")
    assert message_identity(buy_snapshot.signal_id) == rendered.message_id
