from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from smcsignal.analysis import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.liquidity import LiquidityConfig, candle_close_time, load_liquidity_config
from smcsignal.data.config import SUPPORTED_TIMEFRAMES
from tests.liquidity.helpers import analyzer


@pytest.mark.parametrize(
    "value", [Decimal("0"), Decimal("0.00000001"), Decimal("1000"), Decimal("1.0000000000")]
)
def test_valid_tolerances(value):
    assert LiquidityConfig("USDT", value).equal_tolerance_bps == value


@pytest.mark.parametrize(
    "value",
    [
        0,
        True,
        0.1,
        "1",
        None,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-1"),
        Decimal("1000.01"),
        Decimal("1e-9"),
    ],
)
def test_invalid_tolerances(value):
    with pytest.raises(AnalysisConfigurationError):
        LiquidityConfig("USDT", value)


@pytest.mark.parametrize("value", ["", " ", " USDT", None, 5])
def test_invalid_price_units(value):
    with pytest.raises(AnalysisConfigurationError):
        LiquidityConfig(value)


def test_config_is_immutable():
    with pytest.raises(FrozenInstanceError):
        LiquidityConfig("USDT").price_unit = "OTHER"


def test_explicit_liquidity_table(tmp_path):
    path = tmp_path / "test.toml"
    path.write_text(
        '[liquidity]\nprice_unit="USDT"\nequal_tolerance_bps="2.5"\n[unrelated]\nmetadata=true\n'
    )
    assert load_liquidity_config(path) == LiquidityConfig("USDT", Decimal("2.5"))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[liquidity]\n",
        '[liquidity]\nprice_unit="USDT"',
        '[liquidity]\nprice_unit="USDT"\nequal_tolerance_bps=1',
        '[liquidity]\nprice_unit="USDT"\nequal_tolerance_bps="bad"',
        '[liquidity]\nprice_unit="USDT"\nequal_tolerance_bps="0"\nunknown=true',
        '[liquidity]\nprice_unit="USDT"\nequal_tolerance_bps="0"\nscoring=true',
        "invalid TOML",
    ],
)
def test_bad_tables_fail_explicitly(tmp_path, text):
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(AnalysisConfigurationError):
        load_liquidity_config(path)


def test_missing_configuration_file(tmp_path):
    with pytest.raises(AnalysisConfigurationError):
        load_liquidity_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("timeframe", SUPPORTED_TIMEFRAMES)
def test_supported_utc_interval_closures(timeframe):
    opening = datetime(2024, 1, 1, tzinfo=UTC)
    if timeframe == "1M":
        expected = datetime(2024, 2, 1, tzinfo=UTC)
    else:
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        expected = opening + timedelta(seconds=int(timeframe[:-1]) * units[timeframe[-1]])
    assert candle_close_time(opening, timeframe) == expected


@pytest.mark.parametrize(
    "opening,expected",
    [
        (datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC)),
        (datetime(2023, 2, 1, tzinfo=UTC), datetime(2023, 3, 1, tzinfo=UTC)),
        (datetime(2024, 12, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
    ],
)
def test_calendar_month_lengths_and_year_transition(opening, expected):
    assert candle_close_time(opening, "1M") == expected


@pytest.mark.parametrize(
    "opening,timeframe",
    [
        (datetime(2024, 1, 2, tzinfo=UTC), "1M"),
        (datetime(2024, 1, 1, 1, tzinfo=UTC), "1M"),
        (datetime.max.replace(tzinfo=UTC), "15m"),
        (datetime(2024, 1, 1), "15m"),
        (datetime(2024, 1, 1, tzinfo=UTC), "bogus"),
    ],
)
def test_invalid_closure_inputs(opening, timeframe):
    with pytest.raises(AnalysisInputError):
        candle_close_time(opening, timeframe)


def test_analyzer_rejects_unsupported_context():
    original = analyzer()
    with pytest.raises(AnalysisConfigurationError):
        analyzer(series=replace(original.series, timeframe="not_a_timeframe"))


@pytest.mark.parametrize(
    "name", ["example.toml", "binance-public.example.toml", "liquidity.example.toml"]
)
def test_included_liquidity_settings_are_valid(name):
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "config" / name
    assert load_liquidity_config(path) == LiquidityConfig("USDT")
