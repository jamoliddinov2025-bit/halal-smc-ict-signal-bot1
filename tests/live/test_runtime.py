"""Phase 33 live runtime tests: frozen-chain composition and live/offline parity.

The defining guarantee: for the same deterministic candle prefix (primary and
higher timeframes), ``LiveRuntime`` publishes exactly the ``SignalSnapshot``
sequence the frozen offline replay publishes — frame for frame, including the
MTF availability gating — whether the higher-timeframe history is present at
construction, arrives mid-stream, or starts empty.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from smcsignal.analysis.backtest import BacktestConfiguration, ReplayDataset
from smcsignal.analysis.mtf.timeframes import timeframe_seconds
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus
from smcsignal.live import LiveRuntime
from smcsignal.series import series_frames
from tests.backtest.helpers import (
    DATASET_ID,
    PRIMARY_PRICES,
    configuration,
    dataset,
)
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL

SCENARIOS = (PRIMARY_PRICES, RISE_THEN_FALL, CHOP_ONLY)


def series_for(ds: ReplayDataset) -> SeriesProvenance:
    return SeriesProvenance(ds.symbol, ds.timeframe, ds.venue, ds.provider, ds.dataset_id)


def live_frames(
    ds: ReplayDataset, cfg: BacktestConfiguration | None = None
) -> tuple[SignalSnapshot, ...]:
    pipeline = configuration() if cfg is None else cfg
    runtime = LiveRuntime(pipeline, series=series_for(ds), higher_candles=ds.higher_candles)
    return runtime.warm_up(ds.candles)


def assert_parity(prices: tuple) -> None:
    ds = dataset(prices=prices)
    cfg = configuration()
    expected = series_frames(ds, cfg)
    assert expected
    assert live_frames(ds, cfg) == expected


@pytest.mark.parametrize("prices", SCENARIOS)
def test_live_warmup_matches_the_frozen_replay_frame_for_frame(prices: tuple) -> None:
    assert_parity(prices)


@pytest.mark.parametrize("prices", SCENARIOS)
def test_live_frames_publish_real_buy_facts_like_the_replay(prices: tuple) -> None:
    ds = dataset(prices=prices)
    cfg = configuration()
    expected = series_frames(ds, cfg)
    frames = live_frames(ds, cfg)
    buys = tuple(
        index for index, frame in enumerate(frames) if frame.status is SignalStatus.BUY_SIGNAL
    )
    expected_buys = tuple(
        index for index, frame in enumerate(expected) if frame.status is SignalStatus.BUY_SIGNAL
    )
    assert buys == expected_buys
    assert expected_buys  # every audited fixture publishes real BUY facts
    assert all(isinstance(frame, SignalSnapshot) for frame in frames)


def test_signal_generation_is_deterministic_across_instances() -> None:
    ds = dataset()
    cfg = configuration()
    assert live_frames(ds, cfg) == live_frames(ds, cfg)
    # Repeated updates on one runtime equal the batch warm-up.
    runtime = LiveRuntime(cfg, series=series_for(ds), higher_candles=ds.higher_candles)
    incremental = tuple(runtime.update(candle) for candle in ds.candles)
    assert incremental == series_frames(ds, cfg)


def test_warmup_then_update_equals_one_continuous_history() -> None:
    ds = dataset()
    cfg = configuration()
    expected = series_frames(ds, cfg)
    runtime = LiveRuntime(cfg, series=series_for(ds), higher_candles=ds.higher_candles)
    head = list(runtime.warm_up(ds.candles[:6]))
    for candle in ds.candles[6:]:
        head.append(runtime.update(candle))
    assert tuple(head) == expected
    assert runtime.processed_count == len(ds.candles)


def test_htf_context_arriving_mid_stream_matches_the_full_history() -> None:
    """The defining MTF reconstruction test: late HTF candles must not diverge."""

    ds = dataset()
    cfg = configuration()
    expected = series_frames(ds, cfg)
    hour_step = timedelta(seconds=timeframe_seconds("1h"))
    four_hour_step = timedelta(seconds=timeframe_seconds("4h"))
    primary_step = timedelta(seconds=timeframe_seconds("15m"))
    pending = {
        "1h": list(ds.higher_candles["1h"]),
        "4h": list(ds.higher_candles["4h"]),
    }
    runtime = LiveRuntime(
        cfg,
        series=series_for(ds),
        higher_candles={"1h": (), "4h": ()},
    )
    frames: list[SignalSnapshot] = []
    for candle in ds.candles:
        boundary = candle.timestamp + primary_step
        for timeframe, step in (("1h", hour_step), ("4h", four_hour_step)):
            while pending[timeframe] and (pending[timeframe][0].timestamp + step <= boundary):
                runtime.extend_higher(timeframe, pending[timeframe].pop(0))
        frames.append(runtime.update(candle))
    assert tuple(frames) == expected


def test_mtf_gating_is_present_and_uses_completed_htf_context_only() -> None:
    ds = dataset()
    frames = live_frames(ds)
    gated = 0
    for frame in frames:
        mtf_snapshot = frame.upstream.upstream.upstream.upstream
        relations = mtf_snapshot.relations
        assert tuple(relation.timeframe for relation in relations) == ("1h", "4h")
        if any(relation.latest is not None for relation in relations):
            gated += 1
    assert gated > 0  # the fixture's later candles see completed HTF context


def test_halal_filter_gates_non_allowed_symbols() -> None:
    denied = dataset(symbol="DOGEUSDT")
    assert denied.symbol not in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
    frames = live_frames(denied)
    assert frames
    assert all(frame.status is SignalStatus.NO_SIGNAL for frame in frames)
    assert all(frame.candidate.signal.classification.value != "HALAL" for frame in frames)
    allowed = dataset()
    allowed_frames = live_frames(allowed)
    assert any(frame.status is SignalStatus.BUY_SIGNAL for frame in allowed_frames)
    assert all(frame.candidate.signal.classification.value == "HALAL" for frame in allowed_frames)


def test_publish_threshold_is_preserved_from_the_declared_configuration() -> None:
    ds = dataset()
    cfg = configuration(threshold=10)
    frames = live_frames(ds, cfg)
    assert frames
    for frame in frames:
        assert frame.candidate.signal.publish_threshold == 10
        assert frame.settings.publish_threshold == 10
        assert frame.candidate.settings.publish_threshold == 10
    # The frozen cross-validation still holds: SQS and engine thresholds agree.
    assert cfg.setup_quality.publish_threshold == cfg.signal_engine.publish_threshold


def test_runtime_rejects_mismatched_higher_timeframes() -> None:
    ds = dataset()
    with pytest.raises(Exception, match="exactly the configured higher timeframes"):
        LiveRuntime(configuration(), series=series_for(ds), higher_candles={"1h": ()})
    with pytest.raises(Exception, match="chronological and unique"):
        LiveRuntime(
            configuration(),
            series=series_for(ds),
            higher_candles={"1h": ds.higher_candles["1h"][::-1], "4h": ()},
        )


def test_runtime_requires_frozen_types() -> None:
    ds = dataset()
    with pytest.raises(Exception, match="BacktestConfiguration"):
        LiveRuntime(object(), series=series_for(ds), higher_candles=ds.higher_candles)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="SeriesProvenance"):
        LiveRuntime(configuration(), series="not-a-series")  # type: ignore[arg-type]


def test_runtime_matches_the_declared_series_identity() -> None:
    ds = dataset()
    runtime = LiveRuntime(configuration(), series=series_for(ds), higher_candles=ds.higher_candles)
    assert runtime.series == series_for(ds)
    assert runtime.series.dataset_id == DATASET_ID
    assert runtime.configuration.mtf.higher_timeframes == ("1h", "4h")
    assert runtime.higher_candles["1h"] == ds.higher_candles["1h"]
    assert runtime.mtf_config.primary_timeframe == "15m"
