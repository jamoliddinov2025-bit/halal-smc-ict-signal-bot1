import pytest

from smcsignal.analysis import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.halal_filter import (
    AssetClassification,
    FilterMode,
    HalalFilterConfig,
    classify_asset,
)


@pytest.mark.parametrize(
    "symbol", ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "btcusdt", "EthUsdt"]
)
def test_allow_list_marks_listed_assets_halal(symbol):
    result = classify_asset(symbol)
    assert result is AssetClassification.HALAL
    assert result is not AssetClassification.UNKNOWN


@pytest.mark.parametrize("symbol", ["ADAUSDT", "XRPUSDT", "DOGEUSDT", "XYZUSDT", "adausdt"])
def test_allow_list_marks_unlisted_assets_unknown_never_halal(symbol):
    result = classify_asset(symbol)
    assert result is AssetClassification.UNKNOWN
    assert result is not AssetClassification.HALAL
    assert result is not AssetClassification.HARAM


def test_deny_list_marks_denied_assets_haram_and_others_unknown():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT", "DOGEUSDT")
    )
    assert classify_asset("XYZUSDT", config) is AssetClassification.HARAM
    assert classify_asset("xyzusdt", config) is AssetClassification.HARAM
    assert classify_asset("BTCUSDT", config) is AssetClassification.UNKNOWN
    assert classify_asset("BTCUSDT", config) is not AssetClassification.HALAL
    assert classify_asset("ADAUSDT", config) is AssetClassification.UNKNOWN


def test_deny_list_never_emits_halal():
    config = HalalFilterConfig(
        mode=FilterMode.DENY_LIST, allowed_assets=(), denied_assets=("XYZUSDT",)
    )
    for symbol in ("XYZUSDT", "BTCUSDT", "ETHUSDT", "ADAUSDT"):
        assert classify_asset(symbol, config) is not AssetClassification.HALAL


def test_allow_list_never_emits_haram():
    for symbol in ("BTCUSDT", "ADAUSDT"):
        assert classify_asset(symbol) is not AssetClassification.HARAM


@pytest.mark.parametrize("symbol", ["", "B", "BTC-USDT", "BTC USDT", "btc/usdt", "X" * 31])
def test_invalid_symbols_are_rejected(symbol):
    with pytest.raises(AnalysisInputError):
        classify_asset(symbol)


def test_wrong_config_type_is_rejected():
    with pytest.raises(AnalysisConfigurationError):
        classify_asset("BTCUSDT", config=False)  # type: ignore[arg-type]


def test_case_normalization_does_not_invent_unlisted_assets():
    assert classify_asset("btcusdt") is AssetClassification.HALAL
    assert classify_asset("BtCuSdT") is AssetClassification.HALAL
    assert classify_asset("adaUSDT") is AssetClassification.UNKNOWN
