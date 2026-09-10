from smcsignal.analysis.mtf import MTFConfig, MTFDirection, analyze_mtf
from tests.mtf.helpers import BEARISH_HTF, BULLISH_HTF, EIGHT, MIDNIGHT, bars, ote_frames


def nested_trend_direction(frame):
    return frame.upstream.upstream.upstream.upstream.liquidity.context.snapshot.trend


def _join(bullish_tf, other_tf, other_prices):
    primary = ote_frames(bars("15m", (24, 25, 26), start=EIGHT.replace(hour=9)), "15m")
    higher = {
        bullish_tf: ote_frames(bars(bullish_tf, BULLISH_HTF, start=MIDNIGHT), bullish_tf),
        other_tf: ote_frames(bars(other_tf, other_prices, start=MIDNIGHT), other_tf),
    }
    config = MTFConfig(higher_timeframes=(bullish_tf, other_tf))
    return analyze_mtf(primary, higher, config), higher


def test_opposing_ready_htf_trends_are_mixed_without_a_score():
    result, higher = _join("1h", "30m", BEARISH_HTF)
    frame = result[0]
    assert nested_trend_direction(higher["1h"][-1]).ready
    assert nested_trend_direction(higher["1h"][-1]).direction.value == "bullish"
    assert nested_trend_direction(higher["30m"][-1]).direction.value == "bearish"
    assert frame.relations[0].direction is MTFDirection.BULLISH
    assert frame.relations[1].direction is MTFDirection.BEARISH
    assert frame.direction is MTFDirection.MIXED
    assert frame.relations[0].latest is higher["1h"][-1]
    assert frame.relations[1].latest is higher["30m"][-1]


def test_bullish_and_ranging_htf_labels_are_mixed():
    ranging_prices = (15, 10, 16, 20, 18, 12, 17, 18, 14)
    result, higher = _join("1h", "30m", ranging_prices)
    trend = nested_trend_direction(higher["30m"][-1])
    assert trend.ready and trend.direction.value == "ranging"
    assert result[0].relations[0].direction is MTFDirection.BULLISH
    assert result[0].relations[1].direction is MTFDirection.NEUTRAL
    assert result[0].direction is MTFDirection.MIXED


def test_two_bullish_htfs_agree_without_weighting():
    result, higher = _join("1h", "30m", BULLISH_HTF)
    assert result[0].relations[0].direction is MTFDirection.BULLISH
    assert result[0].relations[1].direction is MTFDirection.BULLISH
    assert result[0].direction is MTFDirection.BULLISH
    assert result[0].relations[0].latest is higher["1h"][-1]


def test_all_insufficient_htfs_remain_insufficient():
    primary = ote_frames(bars("15m", (20, 21), start=EIGHT), "15m")
    short = ote_frames(bars("1h", (15, 10), start=MIDNIGHT), "1h")
    result = analyze_mtf(primary, {"1h": short}, MTFConfig(higher_timeframes=("1h",)))
    assert result[0].relations[0].direction is MTFDirection.INSUFFICIENT_CONTEXT
    assert result[0].direction is MTFDirection.INSUFFICIENT_CONTEXT
