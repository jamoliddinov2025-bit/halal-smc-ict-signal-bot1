"""Strict Telegram transport configuration (no secrets).

This configuration controls only *how the Telegram transport sends* (timeouts,
retry budget, backoff, rate limiting, chart on/off, message budget). It carries
**no** token and **no** chat id — tokens come from the approved secret-injection
path at transport construction, and chat ids come from a strict destination map
(see ``destination.py``). No key here can reach signal generation, SMC/ICT
thresholds, indicators, halal rules, Phase 23 governance, optimization, or
execution.

Unknown keys or unsafe values are rejected on load, mirroring the frozen
``load_delivery_config`` / ``load_orchestration_config`` style.

Every field on this surface is *enforced* by the transport. Two 24D-A proposal
fields were resolved in Phase 24D-C1 hardening:

- ``connect_timeout_seconds`` was **removed**. The transport uses the Python
  standard library's single-per-request ``urllib`` timeout, which governs the
  connect and read phases together; a separate connect-only timeout cannot be
  honored without replacing the transport stack. Rather than expose a knob that
  silently does nothing, the setting is gone and ``network_timeout_seconds`` is
  the one documented timeout.
- ``max_message_chars`` is now **enforced** by ``TelegramSink`` as a pre-flight
  caption budget (see ``sink.py``).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError

DEFAULT_NETWORK_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 30.0
DEFAULT_REQUESTS_PER_SECOND = 20.0
DEFAULT_RATE_LIMIT_CAPACITY = 40
DEFAULT_MAX_MESSAGE_CHARS = 4096


def _flag(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise AnalysisConfigurationError(f"{name} must be a boolean")
    return value


def _nonnegative_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise AnalysisConfigurationError(f"{name} must be a nonnegative number")
    return float(value)


def _positive_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise AnalysisConfigurationError(f"{name} must be a positive number")
    return float(value)


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise AnalysisConfigurationError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """Strict, secret-free Telegram transport settings.

    ``enabled`` is the master switch; a transport is only active when
    ``enabled`` is true **and** a token is injected (see ``sink.py``). All other
    fields are transport behavior controls. None of them affects signals.

    ``network_timeout_seconds`` is the single per-request timeout that drives the
    ``UNKNOWN`` versus ``FAILED`` outcome. ``max_message_chars`` is the largest
    caption the transport will attempt to send in one message; a longer caption
    is refused before any network call (default 4096, Telegram's documented
    limit, which the frozen 4000-character presentation budget never reaches).
    """

    enabled: bool = False
    html_parse_mode: bool = True
    chart_enabled: bool = True
    network_timeout_seconds: float = DEFAULT_NETWORK_TIMEOUT_SECONDS
    retry_max_attempts: int = DEFAULT_RETRY_ATTEMPTS
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
    retry_backoff_max_seconds: float = DEFAULT_RETRY_BACKOFF_MAX_SECONDS
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND
    rate_limit_capacity: int = DEFAULT_RATE_LIMIT_CAPACITY
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS

    def __post_init__(self) -> None:
        _flag(self.enabled, "enabled")
        _flag(self.html_parse_mode, "html_parse_mode")
        _flag(self.chart_enabled, "chart_enabled")
        _positive_number(self.network_timeout_seconds, "network_timeout_seconds")
        _positive_int(self.retry_max_attempts, "retry_max_attempts")
        _nonnegative_number(self.retry_backoff_seconds, "retry_backoff_seconds")
        _nonnegative_number(self.retry_backoff_max_seconds, "retry_backoff_max_seconds")
        _positive_number(self.requests_per_second, "requests_per_second")
        _positive_int(self.rate_limit_capacity, "rate_limit_capacity")
        _positive_int(self.max_message_chars, "max_message_chars")


def load_telegram_config(path: str | Path) -> TelegramConfig:
    """Load the exact ``[telegram]`` table; unknown keys are rejected."""
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("telegram")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load telegram configuration: {exc}") from exc
    names = {field.name for field in fields(TelegramConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[telegram] must contain exactly: " + ", ".join(sorted(names))
        )
    return TelegramConfig(**table)
