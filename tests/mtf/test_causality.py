from datetime import datetime, timedelta

from smcsignal.analysis.liquidity import LiquidityAnalyzer
from smcsignal.analysis.mtf import MTFConfig, MTFDirection, analyze_mtf
from tests.mtf.helpers import (
    EIGHT,
    MIDNIGHT,
    bars,
    four_hour_frames,
    hour_frames,
    ote_frames,
    primary_15m,
    run,
    series_for,
)
from tests.ote.helpers import ANALYSIS, DISPLACEMENT, FVG, LIQUIDITY, OB, PD


def test_4h_candle_0800_to_1200_is_unknown_to_15m_observation_at_0900():
    primary = primary_15m()
    four = four_hour_frames()
    assert four[0].provenance.available_at == datetime.fromisoformat("2024-01-01T12:00:00+00:00")
    assert primary[4].upstream.observation.reference.opened_at.hour == 9
    result = analyze_mtf(primary, {"4h": four}, MTFConfig(higher_timeframes=("4h",)))
    assert result[4].relations[0].latest is None
    assert result[4].direction is MTFDirection.INSUFFICIENT_CONTEXT
    assert four[0].provenance.evidence_id not in {
        item.source.evidence_id for item in result[4].evidence
    }


def test_4h_close_at_1200_is_eligible_for_the_15m_bar_that_opens_at_1200():
    four = four_hour_frames()
    result = analyze_mtf(primary_15m(), {"4h": four}, MTFConfig(higher_timeframes=("4h",)))
    noon = result[16]
    opened = noon.upstream.observation.evaluation.reference.opened_at
    assert opened.hour == 12
    assert noon.relations[0].latest is four[0]
    assert four[0].provenance.available_at <= opened


def test_equality_at_the_primary_open_is_eligible():
    hourly = hour_frames()
    # 1h 08:00 closes/available at 09:00; 15m 09:00 opens at 09:00.
    result = analyze_mtf(primary_15m(), {"1h": hourly}, MTFConfig(higher_timeframes=("1h",)))
    nine = result[4]
    assert nine.relations[0].latest is not None
    assert nine.relations[0].latest.upstream.observation.reference.opened_at.hour == 8
    opened = nine.upstream.observation.evaluation.reference.opened_at
    assert nine.relations[0].latest.provenance.available_at == opened


def test_incomplete_or_future_htf_in_the_buffer_cannot_leak_into_earlier_ltf_context():
    full = run()
    without_4h = analyze_mtf(primary_15m(), {"1h": hour_frames(), "4h": ()})
    for earlier in full[:16]:
        assert earlier.relations[1].latest is None
    for left, right in zip(full[:16], without_4h[:16], strict=True):
        left_id = left.relations[0].latest.provenance.evidence_id
        right_id = right.relations[0].latest.provenance.evidence_id
        assert left_id == right_id
        assert left.direction == right.direction
        assert left.provenance.evidence_id == right.provenance.evidence_id


def test_delayed_htf_arrival_is_unusable_until_it_precedes_the_next_ltf_open():
    candles = bars("1h", (15, 10, 16, 20, 18, 12, 17, 22, 19), start=MIDNIGHT)
    liquidity = LiquidityAnalyzer(
        series=series_for("1h"), config=LIQUIDITY, analysis_config=ANALYSIS
    )
    from smcsignal.analysis.displacement import DisplacementAnalyzer
    from smcsignal.analysis.fvg import FVGAnalyzer
    from smcsignal.analysis.order_blocks import OrderBlockAnalyzer
    from smcsignal.analysis.premium_discount import PDAnalyzer
    from tests.ote.helpers import analyzer as ote_analyzer

    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer(FVG)
    blocks = OrderBlockAnalyzer(OB)
    pd = PDAnalyzer(PD)
    ote = ote_analyzer()
    delayed = []
    for index, candle in enumerate(candles):
        known = candle.timestamp + timedelta(hours=1, minutes=5) if index == 8 else None
        delayed.append(
            ote.update(
                pd.update(
                    blocks.update(
                        gaps.update(
                            displacement.update(liquidity.update(candle, available_at=known))
                        )
                    )
                )
            )
        )
    primary = ote_frames(bars("15m", (24, 25), start=EIGHT.replace(hour=9)), "15m")
    result = analyze_mtf(primary, {"1h": delayed}, MTFConfig(higher_timeframes=("1h",)))
    assert delayed[8].provenance.available_at > primary[0].upstream.observation.reference.opened_at
    assert result[0].relations[0].latest is delayed[7]
    assert delayed[8].provenance.available_at <= primary[1].upstream.observation.reference.opened_at
    assert result[1].relations[0].latest is delayed[8]


def test_htf_ids_are_the_original_ote_snapshot_ids():
    hourly = hour_frames()
    result = analyze_mtf(primary_15m(), {"1h": hourly}, MTFConfig(higher_timeframes=("1h",)))
    latest = result[4].relations[0].latest
    assert latest is hourly[8]
    assert latest.provenance.evidence_id == hourly[8].provenance.evidence_id
    assert latest.upstream.provenance.evidence_id == hourly[8].upstream.provenance.evidence_id
