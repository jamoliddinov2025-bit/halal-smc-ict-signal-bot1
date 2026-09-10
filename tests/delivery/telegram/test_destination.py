"""Telegram destination validation/resolution tests."""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.delivery.telegram.destination import (
    destination_redacted,
    resolve_chat_id,
    validate_chat_id,
)


def test_validate_accepts_numeric_and_channel() -> None:
    assert validate_chat_id("-1001234567890") == "-1001234567890"
    assert validate_chat_id("1234567890") == "1234567890"
    assert validate_chat_id("@mychannel") == "@mychannel"


@pytest.mark.parametrize("bad", ["", " ", "abc", "12abc", "@", "-", "1 2"])
def test_validate_rejects_invalid(bad: str) -> None:
    with pytest.raises(AnalysisInputError):
        validate_chat_id(bad)


def test_resolve_maps_logical_to_chat_id() -> None:
    destinations = {"channel-buys": "-1001234567890", "alerts": "@alerts"}
    assert resolve_chat_id("channel-buys", destinations) == "-1001234567890"
    assert resolve_chat_id("alerts", destinations) == "@alerts"


def test_resolve_unknown_destination_raises() -> None:
    with pytest.raises(AnalysisInputError):
        resolve_chat_id("missing", {"channel-buys": "-1001"})


def test_destination_redacted_hides_full_id() -> None:
    redacted = destination_redacted("-1001234567890")
    assert "-1001234567890" not in redacted
    assert "<redacted>" in redacted
