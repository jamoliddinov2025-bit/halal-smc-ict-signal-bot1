"""Phase 35C: gap-aware feed and service integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.message import Message
from urllib.error import HTTPError

import pytest

from smcsignal.data.config import MarketDataConfig
from smcsignal.data.models import OHLCV
from smcsignal.datasets import MemoryDatasetStore
from smcsignal.live import (
    GapAwareLiveMarketFeed,
    LiveGapError,
    RateLimitedFeedError,
    load_live_config,
    start_gap_aware_live_service,
)
from smcsignal.live.market_feed import interval_of
from smcsignal.persistence import MemoryLedgerStore
from tests.backtest.helpers import configuration
from tests.live.test_service import (
    higher_payload,
    kline_from,
    primary_candles,
)

STEP_MS = 15 * 60 * 1000


class ScriptedTransport:
    def __init__(self, responses: dict[str, list[object]]):
        self.responses = {interval: list(items) for interval, items in responses.items()}
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> object:
        interval = interval_of(url)
        self.calls.append(interval)
        queue = self.responses.setdefault(interval, [])
        if not queue:
            return []
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_contiguous_candles_feed():
    transport = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(3)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed = GapAwareLiveMarketFeed(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=100,
        ),
        higher_timeframes=("1h", "4h"),
        transport=transport,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    update = feed.poll()
    assert len(update.primary) == 3


def test_one_missing_candle_feed_gap():
    candles = primary_candles(4)
    payload_missing = [kline_from(c, STEP_MS) for c in (candles[0], candles[1], candles[3])]
    transport = ScriptedTransport(
        {
            "15m": [payload_missing],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed = GapAwareLiveMarketFeed(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=100,
        ),
        higher_timeframes=("1h", "4h"),
        transport=transport,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    with pytest.raises(LiveGapError) as exc:
        feed.poll()
    assert exc.value.gap.missing_count == 1


def test_multiple_missing_candles_feed():
    candles = primary_candles(6)
    payload = [kline_from(c, STEP_MS) for c in (candles[0], candles[1], candles[5])]
    transport = ScriptedTransport(
        {"15m": [payload], "1h": [higher_payload("1h")], "4h": [higher_payload("4h")]}
    )
    feed = GapAwareLiveMarketFeed(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=100,
        ),
        higher_timeframes=("1h", "4h"),
        transport=transport,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    with pytest.raises(LiveGapError) as exc:
        feed.poll()
    assert exc.value.gap.missing_count == 3


def test_no_new_candle_idle():
    p0 = [kline_from(c, STEP_MS) for c in primary_candles(2)]
    transport = ScriptedTransport(
        {"15m": [p0, p0], "1h": [higher_payload("1h")], "4h": [higher_payload("4h")]}
    )
    feed = GapAwareLiveMarketFeed(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=100,
        ),
        higher_timeframes=("1h", "4h"),
        transport=transport,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    first = feed.poll()
    assert len(first.primary) == 2
    second = feed.poll()
    assert second.primary == ()


def test_restart_contiguous_continuation_service():
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    transport1 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(6)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    config = load_live_config(env)
    feed1 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport1,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    s1 = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed1,
    )
    assert s1.primary_window

    transport2 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(7)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed2 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport2,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service2 = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed2,
    )
    transport3 = ScriptedTransport(
        {
            "15m": [
                [kline_from(c, STEP_MS) for c in primary_candles(7)],
                [kline_from(c, STEP_MS) for c in primary_candles(8)],
            ],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed3 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport3,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service2._feed = feed3
    report = service2.run_cycle()
    assert report.frames in (0, 1)


def test_restart_one_missing_candle_service():
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    config = load_live_config(env)
    transport1 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(6)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed1 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport1,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    _s1 = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed1,
    )
    candles = primary_candles(8)
    selected = (
        candles[0],
        candles[1],
        candles[2],
        candles[3],
        candles[4],
        candles[5],
        candles[7],
    )
    payload_gap = [kline_from(c, STEP_MS) for c in selected]
    transport_gap = ScriptedTransport(
        {
            "15m": [payload_gap],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed_gap = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport_gap,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service_gap = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed_gap,
    )
    transport2 = ScriptedTransport(
        {
            "15m": [payload_gap],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed2 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport2,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service_gap._feed = feed2
    with pytest.raises(LiveGapError):
        service_gap.run_cycle()


def test_gap_does_not_mutate_ledger():
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    config = load_live_config(env)
    transport1 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(6)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed1 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport1,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed1,
    )
    ledger_before = store.load(service.ledger_key)
    assert ledger_before is not None
    obs_before = len(ledger_before.observations)
    window_before = window_store.load(service.window_key)
    assert window_before is not None

    candles = primary_candles(8)
    selected = (
        candles[0],
        candles[1],
        candles[2],
        candles[3],
        candles[4],
        candles[5],
        candles[7],
    )
    payload_gap = [kline_from(c, STEP_MS) for c in selected]
    transport_gap = ScriptedTransport(
        {
            "15m": [payload_gap],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed_gap = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport_gap,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service._feed = feed_gap
    with pytest.raises(LiveGapError):
        service.run_cycle()

    ledger_after = store.load(service.ledger_key)
    assert ledger_after is not None
    assert len(ledger_after.observations) == obs_before
    window_after = window_store.load(service.window_key)
    assert window_after == window_before


def test_gap_does_not_mutate_window():
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    config = load_live_config(env)
    transport1 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(6)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed1 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport1,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed1,
    )
    window_before = window_store.load(service.window_key)

    candles = primary_candles(8)
    selected = (
        candles[0],
        candles[1],
        candles[2],
        candles[3],
        candles[4],
        candles[5],
        candles[7],
    )
    payload_gap = [kline_from(c, STEP_MS) for c in selected]
    transport_gap = ScriptedTransport(
        {
            "15m": [payload_gap],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed_gap = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport_gap,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service._feed = feed_gap
    with pytest.raises(LiveGapError):
        service.run_cycle()
    window_after = window_store.load(service.window_key)
    assert window_after == window_before


def test_gap_does_not_invoke_delivery():
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    config = load_live_config(env)
    transport1 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(6)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed1 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport1,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed1,
    )
    seen_before = len(service.delivery.bridge.sink.seen)

    candles = primary_candles(8)
    selected = (
        candles[0],
        candles[1],
        candles[2],
        candles[3],
        candles[4],
        candles[5],
        candles[7],
    )
    payload_gap = [kline_from(c, STEP_MS) for c in selected]
    transport_gap = ScriptedTransport(
        {
            "15m": [payload_gap],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed_gap = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport_gap,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service._feed = feed_gap
    with pytest.raises(LiveGapError):
        service.run_cycle()
    assert len(service.delivery.bridge.sink.seen) == seen_before


def test_successful_retry_after_gap_recovery():
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    config = load_live_config(env)
    transport1 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(6)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed1 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport1,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed1,
    )
    candles = primary_candles(8)
    selected = (
        candles[0],
        candles[1],
        candles[2],
        candles[3],
        candles[4],
        candles[5],
        candles[7],
    )
    payload_gap = [kline_from(c, STEP_MS) for c in selected]
    transport_gap = ScriptedTransport(
        {
            "15m": [payload_gap],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed_gap = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport_gap,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service._feed = feed_gap
    with pytest.raises(LiveGapError):
        service.run_cycle()

    payload_ok = [kline_from(c, STEP_MS) for c in primary_candles(8)]
    transport_ok = ScriptedTransport(
        {"15m": [payload_ok], "1h": [higher_payload("1h")], "4h": [higher_payload("4h")]}
    )
    feed_ok = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport_ok,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service._feed = feed_ok
    report = service.run_cycle()
    assert report.frames == 2


def test_no_duplicate_delivery_after_recovery():
    store = MemoryLedgerStore()
    window_store = MemoryDatasetStore()
    env = {
        "LIVE_SYMBOL": "BTCUSDT",
        "LIVE_TIMEFRAME": "15m",
        "LIVE_ENABLED": "true",
        "LIVE_DRY_RUN": "true",
    }
    config = load_live_config(env)
    transport1 = ScriptedTransport(
        {
            "15m": [[kline_from(c, STEP_MS) for c in primary_candles(6)]],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed1 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport1,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service = start_gap_aware_live_service(
        config,
        store=store,
        window_store=window_store,
        configuration=configuration(),
        feed=feed1,
    )
    transport2 = ScriptedTransport(
        {
            "15m": [
                [kline_from(c, STEP_MS) for c in primary_candles(6)],
                [kline_from(c, STEP_MS) for c in primary_candles(9)],
                [kline_from(c, STEP_MS) for c in primary_candles(9)],
            ],
            "1h": [higher_payload("1h")],
            "4h": [higher_payload("4h")],
        }
    )
    feed2 = GapAwareLiveMarketFeed(
        config.market_data_config(),
        higher_timeframes=config.higher_timeframes,
        transport=transport2,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    service._feed = feed2
    first = service.run_cycle()
    assert first.frames == 0
    second = service.run_cycle()
    assert second.frames == 3
    third = service.run_cycle()
    assert third.frames == 0
    seen_ids = [p.signal_id for p in service.delivery.bridge.sink.seen]
    assert len(seen_ids) == len(set(seen_ids))


def test_429_retry_after_preserved():
    headers = Message()
    headers["Retry-After"] = "10"
    url = (
        "https://data-api.binance.vision/api/v3/klines"
        "?symbol=BTCUSDT&interval=15m&limit=101&endTime=1&timeZone=0"
    )
    err = HTTPError(url, 429, "Too Many Requests", headers, None)
    transport = ScriptedTransport({"15m": [err]})
    feed = GapAwareLiveMarketFeed(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=100,
        ),
        higher_timeframes=(),
        transport=transport,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    with pytest.raises(RateLimitedFeedError) as exc:
        feed.poll()
    assert exc.value.status_code == 429
    assert exc.value.retry_after == "10"
    assert exc.value.retry_after_seconds == 10.0


def test_418_retry_after_preserved():
    headers = Message()
    headers["Retry-After"] = "5"
    url = (
        "https://data-api.binance.vision/api/v3/klines"
        "?symbol=BTCUSDT&interval=15m&limit=101&endTime=1&timeZone=0"
    )
    err = HTTPError(url, 418, "IP banned", headers, None)
    transport = ScriptedTransport({"15m": [err]})
    feed = GapAwareLiveMarketFeed(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=100,
        ),
        higher_timeframes=(),
        transport=transport,
        clock=lambda: datetime(2024, 1, 2, 2, 0, tzinfo=UTC),
        sleep_fn=lambda x: None,
    )
    with pytest.raises(RateLimitedFeedError) as exc:
        feed.poll()
    assert exc.value.status_code == 418
    assert exc.value.retry_after_seconds == 5.0


def test_1000_candle_limit_no_request_above():
    from smcsignal.data import BinancePublicDataProvider

    calls: list[str] = []

    def transport(url: str, timeout: float):
        from urllib.parse import parse_qs, urlparse

        calls.append(parse_qs(urlparse(url).query)["limit"][0])
        return []

    provider = BinancePublicDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            data_source="binance_public",
            history_limit=1000,
        ),
        transport=transport,
        clock=lambda: datetime(2024, 1, 1, 0, 31, tzinfo=UTC),
    )
    provider.fetch_ohlcv()
    assert calls[0] == "1000"


def test_1000_continuity_fails_closed():
    from smcsignal.live.gap import detect_continuity_gap

    last = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    actual = last + timedelta(seconds=900 * 1001)
    new = (
        OHLCV(
            timestamp=actual,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        ),
    )
    gap = detect_continuity_gap(last, new, "15m", history_limit=1000)
    assert gap is not None
    assert gap.missing_count == 1000
    with pytest.raises(LiveGapError):
        raise LiveGapError(gap)


def test_no_pagination_introduced():
    import inspect

    src = inspect.getsource(GapAwareLiveMarketFeed)
    assert "pagination" not in src.lower()
    assert src.count("fetch_ohlcv") <= 3
