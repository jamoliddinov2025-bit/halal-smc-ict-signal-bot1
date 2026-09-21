"""Phase 33 live configuration tests: strict env loading and secret redaction.

All cases pass explicit mappings; ``os.environ`` is exercised once through
``monkeypatch``. No secret material is real, and no component is started.
"""

from __future__ import annotations

import pytest

from smcsignal.live import (
    DEFAULT_HIGHER_TIMEFRAMES,
    LiveConfigurationError,
    LiveServiceConfig,
    load_live_config,
)


def base_env(**overrides: str | None) -> dict[str, str]:
    env: dict[str, str] = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
        "TELEGRAM_BOT_TOKEN": "123456:ABC-secret-token",
        "TELEGRAM_CHAT_ID": "-1001234567890",
    }
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def test_minimal_dry_run_configuration_is_valid() -> None:
    config = load_live_config(
        {
            "LIVE_SYMBOL": "BTCUSDT",
            "LIVE_TIMEFRAME": "15m",
            "LIVE_ENABLED": "true",
        }
    )
    assert config.symbol == "BTCUSDT"
    assert config.timeframe == "15m"
    assert config.enabled is True
    assert config.dry_run is True
    assert config.higher_timeframes == DEFAULT_HIGHER_TIMEFRAMES
    assert config.provider == "binance_public"
    assert config.history_limit == 500
    assert config.token is None
    assert config.chat_id is None


def test_full_real_configuration_is_valid_and_normalizes_symbol() -> None:
    config = load_live_config(base_env(LIVE_SYMBOL="ethusdt", LIVE_DRY_RUN="false"))
    assert config.enabled is True
    assert config.dry_run is False
    assert config.token == "123456:ABC-secret-token"
    assert config.chat_id == "-1001234567890"
    market = config.market_data_config()
    assert market.symbol == "ETHUSDT"  # normalized by the frozen MarketDataConfig
    assert market.data_source == "binance_public"
    assert market.timeframe == "15m"


def test_symbol_and_timeframe_are_required() -> None:
    with pytest.raises(LiveConfigurationError, match="LIVE_SYMBOL is required"):
        load_live_config(base_env(LIVE_SYMBOL=""))
    with pytest.raises(LiveConfigurationError, match="LIVE_TIMEFRAME is required"):
        load_live_config(base_env(LIVE_TIMEFRAME=" "))


def test_missing_token_is_rejected_outside_dry_run() -> None:
    with pytest.raises(LiveConfigurationError, match="TELEGRAM_BOT_TOKEN is required"):
        load_live_config(base_env(LIVE_DRY_RUN="false", TELEGRAM_BOT_TOKEN=None))
    with pytest.raises(LiveConfigurationError, match="TELEGRAM_BOT_TOKEN is required"):
        load_live_config(base_env(LIVE_DRY_RUN="false", TELEGRAM_BOT_TOKEN="   "))


def test_missing_chat_id_is_rejected_when_real_delivery_is_enabled() -> None:
    with pytest.raises(LiveConfigurationError, match="TELEGRAM_CHAT_ID is required"):
        load_live_config(base_env(LIVE_DRY_RUN="false", TELEGRAM_CHAT_ID=None))


def test_invalid_symbol_is_rejected_through_the_frozen_validator() -> None:
    with pytest.raises(LiveConfigurationError, match="symbol"):
        load_live_config(base_env(LIVE_SYMBOL="BTC USDT!"))


def test_invalid_timeframe_is_rejected_through_the_frozen_validator() -> None:
    with pytest.raises(LiveConfigurationError, match="timeframe"):
        load_live_config(base_env(LIVE_TIMEFRAME="7m"))


def test_invalid_higher_timeframes_are_rejected() -> None:
    with pytest.raises(LiveConfigurationError, match="LIVE_HIGHER_TIMEFRAMES"):
        load_live_config(base_env(LIVE_HIGHER_TIMEFRAMES="20m"))
    with pytest.raises(LiveConfigurationError, match="cannot contain the primary timeframe"):
        load_live_config(base_env(LIVE_HIGHER_TIMEFRAMES="15m,1h"))
    with pytest.raises(LiveConfigurationError, match="duplicates"):
        load_live_config(base_env(LIVE_HIGHER_TIMEFRAMES="1h,1h"))
    with pytest.raises(LiveConfigurationError, match="comma-separated"):
        load_live_config(base_env(LIVE_HIGHER_TIMEFRAMES=","))


def test_invalid_chat_id_is_rejected_through_the_frozen_validator() -> None:
    with pytest.raises(LiveConfigurationError, match="chat_id"):
        load_live_config(base_env(TELEGRAM_CHAT_ID="not-a-chat"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("false", False), ("1", True), ("0", False), ("YES", True), ("Off", False)],
)
def test_enabled_and_dry_run_flags_parse_strictly(raw: str, expected: bool) -> None:
    config = load_live_config(base_env(LIVE_ENABLED=raw))
    assert config.enabled is expected


@pytest.mark.parametrize("raw", ["maybe", "2", "truthy"])
def test_invalid_boolean_flags_are_rejected(raw: str) -> None:
    with pytest.raises(LiveConfigurationError, match="boolean flag"):
        load_live_config(base_env(LIVE_ENABLED=raw))


def test_dry_run_does_not_require_a_token() -> None:
    config = load_live_config(base_env(TELEGRAM_BOT_TOKEN=None))
    assert config.enabled is True
    assert config.dry_run is True
    assert config.token is None


def test_disabled_service_does_not_require_token_or_chat() -> None:
    config = load_live_config(
        {
            "LIVE_SYMBOL": "BTCUSDT",
            "LIVE_TIMEFRAME": "15m",
            "LIVE_ENABLED": "false",
        }
    )
    assert config.enabled is False
    assert config.token is None
    assert config.chat_id is None


def test_unsupported_provider_is_rejected() -> None:
    with pytest.raises(LiveConfigurationError, match="data_source"):
        load_live_config(base_env(MARKET_DATA_PROVIDER="yfinance"))


def test_csv_provider_is_rejected_for_live_polling() -> None:
    with pytest.raises(LiveConfigurationError, match="binance_public"):
        load_live_config(base_env(MARKET_DATA_PROVIDER="csv", LIVE_CSV_PATH="/tmp/quotes.csv"))


def test_history_limit_is_validated_through_the_frozen_validator() -> None:
    with pytest.raises(LiveConfigurationError, match="history_limit must be an integer"):
        load_live_config(base_env(LIVE_HISTORY_LIMIT="1001"))
    with pytest.raises(LiveConfigurationError, match="LIVE_HISTORY_LIMIT must be an integer"):
        load_live_config(base_env(LIVE_HISTORY_LIMIT="zero"))
    config = load_live_config(base_env(LIVE_HISTORY_LIMIT="100"))
    assert config.history_limit == 100


def test_secret_redaction_in_repr_and_str() -> None:
    config = load_live_config(base_env())
    for text in (repr(config), str(config), f"{config}"):
        assert "123456:ABC-secret-token" not in text
        assert "-1001234567890" not in text
        assert "token=injected" in text
        assert "chat_id=" in text


def test_load_uses_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_SYMBOL", "ETHUSDT")
    monkeypatch.setenv("LIVE_TIMEFRAME", "5m")
    monkeypatch.setenv("LIVE_ENABLED", "false")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    config = load_live_config()
    assert config.symbol == "ETHUSDT"
    assert config.timeframe == "5m"


def test_direct_construction_rejects_inconsistent_secrets() -> None:
    with pytest.raises(LiveConfigurationError, match="TELEGRAM_BOT_TOKEN is required"):
        LiveServiceConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            higher_timeframes=("1h",),
            provider="binance_public",
            history_limit=500,
            csv_path=None,
            enabled=True,
            dry_run=False,
            token=None,
            chat_id="-1001234567890",
        )
