"""Hand-auditable observations shared by swing/trend/structure tests."""

import pytest

from tests.analysis.helpers import GOLDEN_PRICES, series


@pytest.fixture
def golden_candles():
    return series(GOLDEN_PRICES)
