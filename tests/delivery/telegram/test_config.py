"""Telegram transport config tests (strict, secret-free)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.delivery.telegram import TelegramConfig, load_telegram_config

EXAMPLE_CONFIG = Path(__file__).resolve().parents[3] / "config" / "telegram.example.toml"


def test_defaults_are_safe_and_opt_in() -> None:
    cfg = TelegramConfig()
    assert cfg.enabled is False  # opt-in; disabled until enabled
    assert cfg.html_parse_mode is True
    assert cfg.chart_enabled is True
    assert cfg.retry_max_attempts >= 1
    assert cfg.network_timeout_seconds > 0
    # no secret field exists on the schema
    from dataclasses import fields

    names = {f.name for f in fields(TelegramConfig)}
    for banned in ("token", "secret", "api_key", "chat_id", "bot"):
        assert not any(banned in n for n in names)


@pytest.mark.parametrize(
    "kw",
    [
        dict(enabled="yes"),
        dict(chart_enabled=1),
        dict(retry_max_attempts=0),
        dict(network_timeout_seconds=0),
        dict(requests_per_second=-1),
        dict(rate_limit_capacity=0),
        dict(max_message_chars=0),
    ],
)
def test_config_rejects_unsafe_values(kw: dict) -> None:
    with pytest.raises(AnalysisConfigurationError):
        TelegramConfig(**kw)  # type: ignore[arg-type]


def test_load_config_round_trip(tmp_path) -> None:
    path = tmp_path / "telegram.toml"
    path.write_text(
        "[telegram]\n"
        "enabled = true\n"
        "html_parse_mode = true\n"
        "chart_enabled = true\n"
        "network_timeout_seconds = 10.0\n"
        "retry_max_attempts = 3\n"
        "retry_backoff_seconds = 1.0\n"
        "retry_backoff_max_seconds = 30.0\n"
        "requests_per_second = 20.0\n"
        "rate_limit_capacity = 40\n"
        "max_message_chars = 4096\n",
        encoding="utf-8",
    )
    cfg = load_telegram_config(path)
    assert cfg.enabled is True
    assert cfg.retry_max_attempts == 3
    assert cfg.requests_per_second == 20.0


def test_load_config_rejects_unknown_key(tmp_path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('[telegram]\nenabled = true\nbot_token = "x"\n', encoding="utf-8")
    with pytest.raises(AnalysisConfigurationError):
        load_telegram_config(path)


def test_load_config_missing_file_raises(tmp_path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_telegram_config(tmp_path / "nope.toml")


# --- Phase 24D-C1 D1: no inert config surface -------------------------------


def test_connect_timeout_field_is_removed_from_the_config_surface() -> None:
    """24D-C1 D1: the unimplementable connect-only timeout is gone."""
    names = {field.name for field in dataclasses.fields(TelegramConfig)}
    assert "connect_timeout_seconds" not in names
    assert not hasattr(TelegramConfig, "connect_timeout_seconds")
    # network_timeout_seconds remains the single documented timeout
    assert "network_timeout_seconds" in names


def test_connect_timeout_key_is_now_rejected_as_unknown(tmp_path) -> None:
    """An operator config still carrying the removed key fails loudly."""
    path = tmp_path / "stale.toml"
    path.write_text(
        "[telegram]\n"
        "enabled = true\n"
        "html_parse_mode = true\n"
        "chart_enabled = true\n"
        "network_timeout_seconds = 10.0\n"
        "connect_timeout_seconds = 5.0\n"
        "retry_max_attempts = 3\n"
        "retry_backoff_seconds = 1.0\n"
        "retry_backoff_max_seconds = 30.0\n"
        "requests_per_second = 20.0\n"
        "rate_limit_capacity = 40\n"
        "max_message_chars = 4096\n",
        encoding="utf-8",
    )
    with pytest.raises(AnalysisConfigurationError):
        load_telegram_config(path)


def test_max_message_chars_is_a_real_positive_integer_budget() -> None:
    """24D-C1 D1: the caption budget is part of the enforced surface."""
    assert TelegramConfig().max_message_chars == 4096
    with pytest.raises(AnalysisConfigurationError):
        TelegramConfig(max_message_chars=0)
    with pytest.raises(AnalysisConfigurationError):
        TelegramConfig(max_message_chars=True)  # type: ignore[arg-type]


# --- Phase 24D-C1 D2: the shipped example config ----------------------------


def test_shipped_example_config_loads_and_matches_defaults() -> None:
    """The example is a real, strictly-valid document equal to the defaults."""
    assert EXAMPLE_CONFIG.is_file(), f"missing example config: {EXAMPLE_CONFIG}"
    assert load_telegram_config(EXAMPLE_CONFIG) == TelegramConfig()


def test_shipped_example_config_contains_no_secrets() -> None:
    """D2/security: the example ships no token, no chat id, no secret keys."""
    import re

    text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    assert not re.search(r"\d{6,}:A[A-Za-z0-9_-]{30,}", text)  # token-shaped literal
    assert not re.search(r"^-?\d{9,}$", text, flags=re.MULTILINE)  # chat-id-shaped value
    assert "bot_token" not in text
    assert "chat_id" not in text
    assert "api_key" not in text
