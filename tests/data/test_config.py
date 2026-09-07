"""Configuration contracts, safe defaults, paths, and provider construction."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from smcsignal.data import (
    SUPPORTED_TIMEFRAMES,
    BinancePublicDataProvider,
    CsvDataProvider,
    DataConfigurationError,
    DataProvider,
    MarketDataConfig,
    create_data_provider,
    load_data_config,
)


def settings(**overrides):
    return MarketDataConfig(
        **{
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "data_source": "binance_public",
            "history_limit": 500,
            **overrides,
        }
    )


@pytest.mark.parametrize("timeframe", SUPPORTED_TIMEFRAMES)
def test_supported_timeframes(timeframe) -> None:
    assert settings(timeframe=timeframe).timeframe == timeframe


def test_config_normalizes_symbol_and_is_immutable() -> None:
    config = settings(symbol="btcusdt")
    assert config.symbol == "BTCUSDT"
    with pytest.raises(FrozenInstanceError):
        config.history_limit = 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "BTC/USDT"),
        ("symbol", "BTCUSDT:USDT"),
        ("symbol", ""),
        ("symbol", None),
        ("symbol", "BTC USDT"),
        ("symbol", "BTC&limit=10"),
        ("timeframe", "15M"),
        ("timeframe", "2m"),
        ("timeframe", None),
        ("data_source", "binance_futures"),
        ("data_source", "auto"),
        ("history_limit", 0),
        ("history_limit", 1001),
        ("history_limit", True),
        ("history_limit", "500"),
        ("history_limit", 2.5),
        ("missing_value_policy", "fill"),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", True),
        ("timeout_seconds", "10"),
        ("timeout_seconds", 61),
    ],
)
def test_invalid_settings_fail_before_any_fetch(field, value) -> None:
    with pytest.raises(DataConfigurationError):
        settings(**{field: value})


@pytest.mark.parametrize("limit", [1, 1000])
def test_history_limit_boundaries(limit) -> None:
    assert settings(history_limit=limit).history_limit == limit


@pytest.mark.parametrize("path", [None, "", "  ", 12, "bad\0path"])
def test_csv_requires_a_valid_path(path) -> None:
    with pytest.raises(DataConfigurationError, match="csv_path"):
        settings(data_source="csv", csv_path=path)


def test_binance_rejects_unused_csv_path(tmp_path) -> None:
    with pytest.raises(DataConfigurationError, match="csv_path"):
        settings(csv_path=tmp_path / "unused.csv")


def test_csv_path_resolution_is_independent_of_later_working_directory(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "settings"
    root.mkdir()
    path = root / "data.toml"
    path.write_text(
        '[market_data]\nsymbol="BTCUSDT"\ntimeframe="15m"\ndata_source="csv"\nhistory_limit=2\ncsv_path="../sample.csv"\n'
    )
    monkeypatch.chdir(tmp_path.parent)
    config = load_data_config(path)
    assert config.csv_path == tmp_path / "sample.csv"


@pytest.mark.parametrize(
    "document",
    [
        "",
        "[market_data",
        'market_data="csv"',
        '[other]\nsymbol="BTCUSDT"',
        '[market_data]\nsymbol="BTCUSDT"',
        '[market_data]\nsymbol="BTCUSDT"\ntimeframe="15m"\ndata_source="csv"\nhistory_limit=1\ncsv_path=12',
        '[market_data]\nsymbol="BTCUSDT"\ntimeframe="15m"\ndata_source="binance_public"\nhistory_limit=1\napi_key="not-allowed"',
    ],
)
def test_invalid_toml_schema_is_rejected(tmp_path, document) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text(document)
    with pytest.raises(DataConfigurationError):
        load_data_config(path)


def test_unreadable_toml_is_actionable(tmp_path) -> None:
    with pytest.raises(DataConfigurationError, match="cannot load"):
        load_data_config(tmp_path / "missing.toml")


def test_example_config_constructs_working_offline_provider() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config" / "example.toml"
    config = load_data_config(config_path)
    provider = create_data_provider(config)
    assert isinstance(provider, CsvDataProvider)
    assert len(provider.fetch_ohlcv().candles) == 5


def test_factory_creates_binance_provider_without_network_access() -> None:
    assert isinstance(create_data_provider(settings()), BinancePublicDataProvider)


def test_interface_is_abstract() -> None:
    with pytest.raises(TypeError):
        DataProvider()


def test_concrete_providers_reject_wrong_sources(fixture_csv) -> None:
    with pytest.raises(DataConfigurationError):
        CsvDataProvider(settings())
    with pytest.raises(DataConfigurationError):
        BinancePublicDataProvider(settings(data_source="csv", csv_path=fixture_csv))


def test_extremely_large_timeout_is_a_configuration_error() -> None:
    with pytest.raises(DataConfigurationError, match="timeout_seconds"):
        settings(timeout_seconds=10**400)


def test_provider_configuration_cannot_be_rebound(fixture_csv) -> None:
    provider = create_data_provider(settings(data_source="csv", csv_path=fixture_csv))
    with pytest.raises(AttributeError):
        provider.config = settings()


def test_public_binance_example_loads_without_network_access() -> None:
    path = Path(__file__).resolve().parents[2] / "config" / "binance-public.example.toml"
    config = load_data_config(path)
    assert config.data_source == "binance_public"
    assert isinstance(create_data_provider(config), BinancePublicDataProvider)
