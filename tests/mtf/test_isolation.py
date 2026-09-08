from dataclasses import replace

import pytest

from smcsignal.analysis import AnalysisInputError, SeriesProvenance
from smcsignal.analysis.mtf import MTFAnalyzer, MTFConfig, analyze_mtf
from tests.mtf.helpers import (
    BULLISH_HTF,
    EIGHT,
    MIDNIGHT,
    bars,
    four_hour_frames,
    hour_frames,
    ote_frames,
    primary_15m,
    series_for,
)


def test_mixed_symbols_across_htfs_are_rejected():
    eth = ote_frames(
        bars("1h", BULLISH_HTF, start=MIDNIGHT),
        "1h",
        series=SeriesProvenance("ETHUSDT", "1h", "synthetic_spot", "csv", "phase13-eth"),
    )
    with pytest.raises(AnalysisInputError):
        MTFAnalyzer(higher={"1h": eth, "4h": four_hour_frames()})


def test_primary_symbol_must_match_htf_symbol():
    eth_primary = ote_frames(
        bars("15m", (20, 21), start=EIGHT),
        "15m",
        series=SeriesProvenance("ETHUSDT", "15m", "synthetic_spot", "csv", "phase13-eth"),
    )
    with pytest.raises(AnalysisInputError):
        analyze_mtf(eth_primary, {"1h": hour_frames(), "4h": four_hour_frames()})


def test_wrong_htf_mapping_key_is_rejected():
    with pytest.raises(AnalysisInputError):
        MTFAnalyzer(higher={"1h": hour_frames()})
    with pytest.raises(AnalysisInputError):
        MTFAnalyzer(higher={"1h": hour_frames(), "4h": four_hour_frames(), "1d": ()})


def test_htf_frames_with_mismatched_series_timeframe_are_rejected():
    relabeled = ote_frames(bars("1h", BULLISH_HTF, start=MIDNIGHT), "1h")
    with pytest.raises(AnalysisInputError):
        MTFAnalyzer(MTFConfig(higher_timeframes=("4h",)), higher={"4h": relabeled})


def test_primary_timeframe_must_match_configuration():
    hourly_as_primary = ote_frames(bars("1h", BULLISH_HTF, start=MIDNIGHT), "1h")
    with pytest.raises(AnalysisInputError):
        analyze_mtf(hourly_as_primary, {"1h": hour_frames(), "4h": four_hour_frames()})


def test_non_ote_or_shifted_primary_indices_are_rejected():
    engine = MTFAnalyzer(higher={"1h": hour_frames(), "4h": four_hour_frames()})
    source = primary_15m()
    with pytest.raises(AnalysisInputError):
        engine.update(source[1])
    engine.update(source[0])
    with pytest.raises(AnalysisInputError):
        engine.update(source[0])


def test_dataset_id_may_differ_across_timeframes():
    hourly = ote_frames(
        bars("1h", BULLISH_HTF + (18, 17, 16), start=MIDNIGHT),
        "1h",
        series=replace(series_for("1h"), dataset_id="phase13-1h"),
    )
    four = ote_frames(
        bars("4h", (15,), start=EIGHT),
        "4h",
        series=replace(series_for("4h"), dataset_id="phase13-4h"),
    )
    result = analyze_mtf(primary_15m(), {"1h": hourly, "4h": four})
    assert result[4].relations[0].latest is not None
    assert result[4].relations[0].latest.provenance.series.dataset_id == "phase13-1h"
