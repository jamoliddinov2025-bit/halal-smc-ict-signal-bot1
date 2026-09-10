"""Shared fixtures for the Telegram transport tests (offline, no real calls)."""

from __future__ import annotations

import pytest

from smcsignal.delivery.telegram import TelegramConfig, TelegramHttpClient, TelegramSink

from ._helpers import FakeHttpTransport


@pytest.fixture
def fake_transport() -> FakeHttpTransport:
    return FakeHttpTransport()


@pytest.fixture
def telegram_client(fake_transport: FakeHttpTransport) -> TelegramHttpClient:
    return TelegramHttpClient("TEST:TOKEN123", transport=fake_transport, timeout=5.0)


def _default_config() -> TelegramConfig:
    return TelegramConfig(enabled=True, retry_max_attempts=1)


@pytest.fixture
def telegram_sink(telegram_client: TelegramHttpClient) -> TelegramSink:
    return TelegramSink(
        "TEST:TOKEN123",
        config=_default_config(),
        destinations={"channel-buys": "-1001234567890"},
        http_client=telegram_client,
    )
