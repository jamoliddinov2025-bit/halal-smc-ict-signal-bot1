"""Phase 33 live-service configuration: strict environment loading, secrets never exposed.

The live service is wired from a small, explicit set of environment variables.
Loading is strict: unknown values, unsupported timeframes, and malformed flags
are rejected before any component is constructed, and every market-data or
Telegram value is validated by routing it through the existing frozen
constructors (:class:`smcsignal.data.MarketDataConfig`,
:class:`smcsignal.delivery.telegram.destination.validate_chat_id`) — never by
duplicating their rules. No third-party environment file loader is added;
the boundary reads a mapping (``os.environ`` by default) with the standard
library only.

Secrets: ``TELEGRAM_BOT_TOKEN`` is held in a field excluded from ``repr`` and
``str``; the configured chat id is rendered through the existing redaction
helper. Neither value is ever written to a repository configuration file —
repository TOML files stay secret-free exactly as Phase 24 established.

Nothing here performs IO, network calls, signal generation, delivery, or any
trading/execution action. Loading a configuration starts nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from smcsignal.analysis.mtf.timeframes import require_higher_multiple, timeframe_seconds
from smcsignal.data.config import DataSource, MarketDataConfig
from smcsignal.delivery.telegram.destination import validate_chat_id

DEFAULT_PROVIDER = "binance_public"
DEFAULT_HISTORY_LIMIT = 500
DEFAULT_HIGHER_TIMEFRAMES = ("1h", "4h")

_TRUE_FLAGS = frozenset({"1", "true", "yes", "on"})
_FALSE_FLAGS = frozenset({"0", "false", "no", "off"})

_SYMBOL = "LIVE_SYMBOL"
_TIMEFRAME = "LIVE_TIMEFRAME"
_HIGHER = "LIVE_HIGHER_TIMEFRAMES"
_PROVIDER = "MARKET_DATA_PROVIDER"
_HISTORY = "LIVE_HISTORY_LIMIT"
_CSV_PATH = "LIVE_CSV_PATH"
_ENABLED = "LIVE_ENABLED"
_DRY_RUN = "LIVE_DRY_RUN"
_TOKEN = "TELEGRAM_BOT_TOKEN"
_CHAT_ID = "TELEGRAM_CHAT_ID"


class LiveConfigurationError(ValueError):
    """The live-service environment configuration is missing or invalid."""


def _flag(raw: str, name: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUE_FLAGS:
        return True
    if lowered in _FALSE_FLAGS:
        return False
    raise LiveConfigurationError(f"{name} must be a boolean flag (true/false), got {raw!r}")


def _required(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if value is None or not value.strip():
        raise LiveConfigurationError(f"{name} is required for the live service")
    return value


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name)
    if value is None or not value.strip():
        return None
    return value


@dataclass(frozen=True, slots=True)
class LiveServiceConfig:
    """One validated live-service configuration.

    ``token`` is deliberately excluded from ``repr``/``str`` so the secret can
    never leak through logging; ``chat_id`` is redacted in ``repr``/``str``
    with the existing Telegram audit helper. Every field is plain validated
    data; constructing this configuration starts nothing.
    """

    symbol: str
    timeframe: str
    higher_timeframes: tuple[str, ...]
    provider: str
    history_limit: int
    csv_path: Path | None
    enabled: bool
    dry_run: bool
    token: str | None = field(default=None, repr=False, compare=False)
    chat_id: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise LiveConfigurationError("symbol must be a nonempty string")
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise LiveConfigurationError("timeframe must be a nonempty string")
        if not isinstance(self.higher_timeframes, tuple) or not self.higher_timeframes:
            raise LiveConfigurationError("higher_timeframes must be a nonempty tuple")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise LiveConfigurationError("provider must be a nonempty string")
        if type(self.history_limit) is not int or not 1 <= self.history_limit <= 1000:
            raise LiveConfigurationError("history_limit must be an integer from 1 to 1000")
        if self.csv_path is not None and not isinstance(self.csv_path, Path):
            raise LiveConfigurationError("csv_path must be a Path or None")
        if type(self.enabled) is not bool or type(self.dry_run) is not bool:
            raise LiveConfigurationError("enabled and dry_run must be booleans")
        if self.token is not None and (not isinstance(self.token, str) or not self.token.strip()):
            raise LiveConfigurationError("token must be a nonempty string or None")
        if self.chat_id is not None and (
            not isinstance(self.chat_id, str) or not self.chat_id.strip()
        ):
            raise LiveConfigurationError("chat_id must be a nonempty string or None")
        if self.enabled and not self.dry_run:
            if self.token is None:
                raise LiveConfigurationError(
                    "TELEGRAM_BOT_TOKEN is required when the live service is enabled "
                    "outside dry-run"
                )
            if self.chat_id is None:
                raise LiveConfigurationError(
                    "TELEGRAM_CHAT_ID is required when the live service is enabled outside dry-run"
                )

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        from smcsignal.delivery.telegram.audit import redact_chat_id

        chat = "None" if self.chat_id is None else redact_chat_id(self.chat_id)
        token = "injected" if self.token is not None else "None"
        return (
            f"LiveServiceConfig(symbol={self.symbol!r}, timeframe={self.timeframe!r}, "
            f"higher_timeframes={self.higher_timeframes!r}, provider={self.provider!r}, "
            f"history_limit={self.history_limit}, csv_path={self.csv_path!r}, "
            f"enabled={self.enabled!r}, dry_run={self.dry_run!r}, token={token}, "
            f"chat_id={chat})"
        )

    def market_data_config(self) -> MarketDataConfig:
        """The validated Phase 2 market-data configuration for the primary feed."""

        return MarketDataConfig(
            symbol=self.symbol,
            timeframe=self.timeframe,
            data_source=cast(DataSource, self.provider),
            history_limit=self.history_limit,
            csv_path=self.csv_path,
        )


def load_live_config(source: Mapping[str, str] | None = None) -> LiveServiceConfig:
    """Load and validate the live-service environment mapping.

    ``source`` defaults to ``os.environ``. Values are validated through the
    existing strict constructors — ``MarketDataConfig`` for the symbol,
    timeframe, provider, history window, and CSV path; ``validate_chat_id``
    for the Telegram destination; the MTF timeframe helpers for the higher
    timeframes. A configuration that fails validation starts nothing.
    """

    env = os.environ if source is None else source
    symbol = _required(env, _SYMBOL)
    timeframe = _required(env, _TIMEFRAME)
    provider = _optional(env, _PROVIDER) or DEFAULT_PROVIDER
    raw_history = _optional(env, _HISTORY)
    if raw_history is None:
        history_limit = DEFAULT_HISTORY_LIMIT
    else:
        try:
            history_limit = int(raw_history)
        except ValueError as exc:
            raise LiveConfigurationError(
                f"{_HISTORY} must be an integer, got {raw_history!r}"
            ) from exc
    raw_csv = _optional(env, _CSV_PATH)
    csv_path = Path(raw_csv) if raw_csv is not None else None
    enabled = _flag(_optional(env, _ENABLED) or "false", _ENABLED)
    dry_run = _flag(_optional(env, _DRY_RUN) or "true", _DRY_RUN)
    token = _optional(env, _TOKEN)
    raw_chat = _optional(env, _CHAT_ID)
    if raw_chat is None:
        chat_id = None
    else:
        try:
            chat_id = validate_chat_id(raw_chat)
        except ValueError as exc:
            raise LiveConfigurationError(str(exc)) from exc

    raw_higher = _optional(env, _HIGHER)
    if raw_higher is None:
        higher: tuple[str, ...] = DEFAULT_HIGHER_TIMEFRAMES
    else:
        parts = tuple(item.strip() for item in raw_higher.split(",") if item.strip())
        if not parts:
            raise LiveConfigurationError(
                f"{_HIGHER} must be a comma-separated list of higher timeframes"
            )
        higher = parts
    seen: set[str] = set()
    for item in higher:
        if item == timeframe:
            raise LiveConfigurationError(f"{_HIGHER} cannot contain the primary timeframe")
        if item in seen:
            raise LiveConfigurationError(f"{_HIGHER} cannot contain duplicates: {item}")
        seen.add(item)
        try:
            timeframe_seconds(item)
            require_higher_multiple(timeframe, item)
        except ValueError as exc:
            raise LiveConfigurationError(f"{_HIGHER} entry {item!r} is invalid: {exc}") from exc

    try:
        # Validate the market-data values through the existing frozen constructor.
        market = MarketDataConfig(
            symbol=symbol,
            timeframe=timeframe,
            data_source=cast(DataSource, provider),
            history_limit=history_limit,
            csv_path=csv_path,
        )
    except ValueError as exc:
        raise LiveConfigurationError(str(exc)) from exc
    if market.data_source != DEFAULT_PROVIDER:
        raise LiveConfigurationError(
            "live polling requires the binance_public market data provider; "
            f"got {market.data_source!r}"
        )

    return LiveServiceConfig(
        symbol=market.symbol,
        timeframe=market.timeframe,
        higher_timeframes=higher,
        provider=market.data_source,
        history_limit=market.history_limit,
        csv_path=None,
        enabled=enabled,
        dry_run=dry_run,
        token=token,
        chat_id=chat_id,
    )
