from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from smcsignal.analysis import (
    OutcomeStatus,
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
    SignalStatus,
    analyze_outcome_tracking,
    load_analysis_config,
    load_halal_filter_config,
    load_liquidity_config,
    load_mtf_config,
    load_outcome_tracking_config,
)
from smcsignal.analysis.displacement import load_displacement_config
from smcsignal.analysis.fvg import load_fvg_config
from smcsignal.analysis.halal_filter import analyze_halal
from smcsignal.analysis.mtf import analyze_mtf
from smcsignal.analysis.order_blocks import load_order_block_config
from smcsignal.analysis.ote import analyze_ote, load_ote_config
from smcsignal.analysis.premium_discount import load_pd_config
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    analyze_signal_eligibility,
    load_signal_eligibility_config,
)
from smcsignal.analysis.signal_engine import (
    SignalEngineConfig,
    analyze_signal_engine,
    load_signal_engine_config,
)
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config
from tests.halal_filter.helpers import mtf_for, series
from tests.halal_filter.helpers import run as halal_run
from tests.mtf.helpers import series_for
from tests.ote.helpers import upstream
from tests.outcome_tracking.helpers import completed_records, outcome_for, signal_frames

PATH = Path(__file__).resolve().parents[2] / "config" / "outcome-tracking.example.toml"
ROOT = Path(__file__).resolve().parents[2]


def _ote(candles, timeframe: str, *, symbol: str = "BTCUSDT"):
    declared = series(symbol, timeframe) if symbol != "BTCUSDT" else series_for(timeframe)
    return analyze_ote(
        upstream(
            candles,
            series=declared,
            analysis=load_analysis_config(PATH),
            liquidity=load_liquidity_config(PATH),
            displacement=load_displacement_config(PATH),
            fvg=load_fvg_config(PATH),
            order_blocks=load_order_block_config(PATH),
            pd=load_pd_config(PATH),
        ),
        load_ote_config(PATH),
    )


def documented_frames(threshold: int):
    data = load_data_config(PATH)
    mtf = load_mtf_config(PATH)
    halal = load_halal_filter_config(PATH)
    eligibility = load_signal_eligibility_config(PATH)
    primary_candles = CsvDataProvider(data).fetch_ohlcv().candles
    hourly = (
        CsvDataProvider(
            MarketDataConfig(
                symbol="BTCUSDT",
                timeframe="1h",
                data_source="csv",
                history_limit=500,
                csv_path=ROOT / "tests/fixtures/mtf-1h.csv",
            )
        )
        .fetch_ohlcv()
        .candles
    )
    four = (
        CsvDataProvider(
            MarketDataConfig(
                symbol="BTCUSDT",
                timeframe="4h",
                data_source="csv",
                history_limit=500,
                csv_path=ROOT / "tests/fixtures/mtf-4h.csv",
            )
        )
        .fetch_ohlcv()
        .candles
    )
    primary = _ote(primary_candles, "15m")
    frames = {"1h": _ote(hourly, "1h"), "4h": _ote(four, "4h")}
    scored = analyze_setup_quality(
        analyze_halal(analyze_mtf(primary, frames, mtf), halal),
        SetupQualityConfig(threshold),
    )
    eligible = analyze_signal_eligibility(scored, eligibility)
    engine = (
        load_signal_engine_config(PATH)
        if threshold == 75
        else SignalEngineConfig(publish_threshold=threshold)
    )
    return analyze_signal_engine(eligible, engine)


def test_documented_csv_default_pipeline_has_zero_outcomes() -> None:
    frames = documented_frames(75)
    assert all(frame.status is SignalStatus.NO_SIGNAL for frame in frames)
    snapshots = analyze_outcome_tracking(frames, load_outcome_tracking_config(PATH))
    tracker = OutcomeTrackingAnalyzer(load_outcome_tracking_config(PATH))
    streamed = tuple(tracker.update(frame) for frame in frames)
    assert streamed == snapshots
    assert tracker.open_outcomes == () and tracker.finalized_outcomes == ()
    assert snapshots[-1].analytics.total_buy_signals == 0
    assert snapshots[0].upstream is frames[0]


def test_documented_csv_threshold10_pipeline_produces_hand_computed_win() -> None:
    frames = documented_frames(10)
    buys = [index for index, frame in enumerate(frames) if frame.status is SignalStatus.BUY_SIGNAL]
    assert buys == [4, 8, 12, 16]
    snapshots = analyze_outcome_tracking(frames, OutcomeTrackingConfig(horizon_bars=10))
    completed = completed_records(snapshots)
    assert len(completed) == 1
    record = completed[0]
    assert record.status is OutcomeStatus.WIN
    assert record.reference_close == Decimal(24)
    assert record.final_close == Decimal(34) and record.final_index == 14
    assert record.mfe_price == Decimal(35) and record.mfe_index == 14
    assert record.mae_price == Decimal(24) and record.mae_index == 5
    summary = snapshots[-1].analytics
    assert summary.total_buy_signals == 4
    assert summary.finalized_count == 1 and summary.open_count == 3
    assert summary.win_count == 1 and summary.win_rate == Decimal(1)
    tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig(horizon_bars=10))
    for frame in frames:
        tracker.update(frame)
    versions = outcome_for(snapshots, frames[4].signal_id)
    assert [version.candles_observed for version in versions] == list(range(11))
    assert tracker.latest is not None and tracker.latest.upstream is frames[-1]


def test_csv_pipeline_matches_the_helper_chain_outcomes() -> None:
    csv_frames = documented_frames(10)
    helper_frames = signal_frames()
    csv_snapshots = analyze_outcome_tracking(csv_frames, OutcomeTrackingConfig(horizon_bars=10))
    helper_snapshots = analyze_outcome_tracking(
        helper_frames, OutcomeTrackingConfig(horizon_bars=10)
    )
    assert len(csv_snapshots) == len(helper_snapshots) == 17

    def semantic(snapshots):
        completed = [record for snapshot in snapshots for record in snapshot.completed]
        return (
            [
                (
                    record.status,
                    record.reference_close,
                    record.final_close,
                    record.final_index,
                    record.mfe_price,
                    record.mfe_index,
                    record.mae_price,
                    record.mae_index,
                )
                for record in completed
            ],
            snapshots[-1].analytics,
        )

    assert semantic(csv_snapshots) == semantic(helper_snapshots)
    assert csv_snapshots[-1].analytics.win_count == 1
    assert csv_snapshots[-1].analytics.open_count == 3


def test_lowercase_series_symbol_preserves_outcome_identity() -> None:
    scored = analyze_setup_quality(halal_run(mtf_for("btcusdt")), SetupQualityConfig(10))
    eligible = analyze_signal_eligibility(scored)
    frames = analyze_signal_engine(eligible, SignalEngineConfig(publish_threshold=10))
    snapshots = analyze_outcome_tracking(frames, OutcomeTrackingConfig(horizon_bars=3))
    created = [record for snapshot in snapshots for record in snapshot.created]
    assert created
    for record in created:
        assert record.symbol == "btcusdt"
        assert record.provenance.series.symbol == "btcusdt"
    assert snapshots[0].provenance.series.symbol == "btcusdt"


def test_public_exports_are_importable_in_isolation() -> None:
    from smcsignal.analysis.outcome_tracking import (  # noqa: F401
        AnalyticsSummary,
        OutcomeSnapshot,
        OutcomeStatus,
        OutcomeTrackingAnalyzer,
        OutcomeTrackingConfig,
        SignalOutcome,
        analyze_outcome_tracking,
        load_outcome_tracking_config,
    )
