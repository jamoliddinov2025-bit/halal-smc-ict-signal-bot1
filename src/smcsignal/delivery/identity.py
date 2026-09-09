"""Deterministic delivery-layer identities.

Identities are content digests over stable inputs only. They never depend on a
clock, a random UUID, a process id, a memory address, or machine-specific
values, so they are stable across process restarts, repeated formatting, and
repeated delivery attempts. The message identity is ultimately bound to the
immutable upstream ``signal_id``.
"""

from __future__ import annotations

from smcsignal.analysis.liquidity.evidence import digest

METHODOLOGY_VERSION = "delivery-v1"


def message_identity(signal_id: str) -> str:
    """Deterministic identity of one rendered signal message.

    Bound to the immutable upstream signal id plus the presentation profile so
    the same signal under the same profile always maps to the same message id.
    """
    if not isinstance(signal_id, str) or not signal_id.strip():
        raise ValueError("signal_id must be a nonempty string")
    return "message:" + digest(
        {
            "methodology": METHODOLOGY_VERSION,
            "kind": "signal-message",
            "signal_id": signal_id,
        }
    )


def delivery_identity(message_id: str, destination_id: str) -> str:
    """Deterministic identity of one delivery of a message to one destination.

    ``destination_id`` is a stable logical identifier of the destination (for
    example a configured channel key); it is never the raw secret chat id and
    never the bot token. Two deliveries of the same message to the same
    destination always share one identity, so retries are not new logical
    deliveries.
    """
    if not isinstance(message_id, str) or not message_id.startswith("message:"):
        raise ValueError("message_id must be a message: identity")
    if not isinstance(destination_id, str) or not destination_id.strip():
        raise ValueError("destination_id must be a nonempty string")
    return "delivery:" + digest(
        {
            "methodology": METHODOLOGY_VERSION,
            "kind": "message-delivery",
            "message_id": message_id,
            "destination_id": destination_id,
        }
    )
