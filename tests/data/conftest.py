"""Shared synthetic records; never live market data."""

from pathlib import Path

import pytest


@pytest.fixture
def raw_row() -> dict[str, object]:
    return {
        "timestamp": "2024-01-01T00:00:00Z",
        "open": "100.00",
        "high": "110.00",
        "low": "90.00",
        "close": "105.00",
        "volume": "10.50",
    }


@pytest.fixture
def fixture_csv() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "ohlcv.csv"
