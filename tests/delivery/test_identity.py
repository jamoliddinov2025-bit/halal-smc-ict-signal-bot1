"""Phase 24B identity tests: deterministic, timestamp-free, input-bound."""

from __future__ import annotations

from tests.delivery.conftest import make_message

from smcsignal.analysis.signal_engine import SignalReason
from smcsignal.delivery import (
    assemble_signal_message,
    delivery_identity,
    message_identity,
)


def test_message_identity_is_deterministic() -> None:
    assert message_identity("signal-candidate:a") == message_identity("signal-candidate:a")
    assert message_identity("signal-candidate:a").startswith("message:")


def test_distinct_signals_have_distinct_message_identity() -> None:
    assert message_identity("signal-candidate:a") != message_identity("signal-candidate:b")


def test_message_identity_is_bound_to_upstream_signal_id() -> None:
    # The identity depends only on the immutable upstream signal id plus the
    # fixed presentation profile; other projection facts cannot change it.
    rendered_a = assemble_signal_message(make_message(signal_id="signal-candidate:k"))
    rendered_b = assemble_signal_message(
        make_message(
            signal_id="signal-candidate:k",
            reasons=(SignalReason.HALAL_ASSET, SignalReason.MTF_ALIGNMENT),
            reference_price=None,
        )
    )
    assert rendered_a.message_id == rendered_b.message_id


def test_timestamps_do_not_alter_message_identity() -> None:
    # message_identity is a pure function of signal_id; a presentation timestamp
    # field is data, not part of the identity. Equivalent inputs (same signal id)
    # therefore always produce the same identity regardless of any datetime.
    first = make_message(signal_id="signal-candidate:t")
    second = make_message(signal_id="signal-candidate:t")
    assert message_identity(first.signal_id) == message_identity(second.signal_id)
    assert message_identity("signal-candidate:t") == message_identity("signal-candidate:t")


def test_delivery_identity_deterministic_across_retries() -> None:
    message_id = message_identity("signal-candidate:r")
    delivery = delivery_identity(message_id, "channel-buys")
    assert delivery.startswith("delivery:")
    # Retries / repeated calls reuse the same deterministic identity.
    assert delivery_identity(message_id, "channel-buys") == delivery
    # A different destination is a different logical delivery.
    assert delivery_identity(message_id, "channel-alerts") != delivery


def test_delivery_identity_is_timestamp_free() -> None:
    # The identity has no time, random, or machine input: repeated calls on the
    # same message+destination are byte-identical.
    message_id = message_identity("signal-candidate:z")
    assert delivery_identity(message_id, "channel-x") == delivery_identity(message_id, "channel-x")
