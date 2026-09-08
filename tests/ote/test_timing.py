from datetime import timedelta

from smcsignal.analysis.displacement import DisplacementAnalyzer
from smcsignal.analysis.fvg import FVGAnalyzer
from smcsignal.analysis.liquidity import LiquidityAnalyzer
from smcsignal.analysis.order_blocks import OrderBlockAnalyzer
from smcsignal.analysis.ote import OTEClassification
from smcsignal.analysis.premium_discount import PDAnalyzer
from tests.ote.helpers import (
    ANALYSIS,
    DISPLACEMENT,
    LIQUIDITY,
    SERIES,
    analyzer,
    golden,
    run,
)


def test_range_creating_candle_cannot_use_the_new_zone():
    frames = run(golden())
    assert frames[4].zone is not None
    assert frames[4].zone.confirmation_index == 4
    assert frames[4].classification == OTEClassification.INSUFFICIENT_CONTEXT
    assert frames[5].zone is frames[4].zone
    assert frames[5].observation.zone is frames[4].zone
    assert frames[5].classification == OTEClassification.INSIDE_OTE


def test_zone_available_at_is_the_range_publication_instant_not_later_closes():
    frames = run(golden())
    assert frames[4].zone.available_at == frames[4].upstream.dealing_range.available_at
    assert frames[4].zone.available_at == frames[4].upstream.observation.available_at
    assert frames[5].observation.available_at == frames[5].upstream.observation.available_at
    assert frames[5].zone.available_at < frames[5].observation.available_at


def test_delayed_range_arrival_is_unusable_until_it_precedes_the_bar_open():
    liquidity = LiquidityAnalyzer(series=SERIES, config=LIQUIDITY, analysis_config=ANALYSIS)
    displacement = DisplacementAnalyzer(DISPLACEMENT, price_unit="USDT")
    gaps = FVGAnalyzer()
    blocks = OrderBlockAnalyzer()
    pd = PDAnalyzer()
    ote = analyzer()
    result = []
    for i, candle in enumerate(golden()):
        known = candle.timestamp + timedelta(minutes=16, microseconds=1) if i == 4 else None
        frame = ote.update(
            pd.update(
                blocks.update(
                    gaps.update(displacement.update(liquidity.update(candle, available_at=known)))
                )
            )
        )
        result.append(frame)
    assert result[4].zone is not None
    assert result[4].zone.available_at > result[4].upstream.observation.reference.closed_at
    assert result[4].classification == OTEClassification.INSUFFICIENT_CONTEXT
    # Candle 5 opens before the delayed range arrived, so it still cannot use it.
    assert result[5].zone.available_at > result[5].upstream.observation.reference.opened_at
    assert result[5].classification == OTEClassification.INSUFFICIENT_CONTEXT
    assert result[6].zone.available_at <= result[6].upstream.observation.reference.opened_at
    assert result[6].classification == OTEClassification.BELOW_OTE


def test_historical_observation_is_not_rewritten_when_a_later_range_appears():
    from tests.analysis.helpers import bar
    from tests.premium_discount.helpers import golden as pd_golden

    candles = (*pd_golden(), bar(7, 12, high=13, low=9))
    frames = run(candles)
    earlier = frames[5]
    later = frames[7]
    assert earlier.classification == OTEClassification.INSUFFICIENT_CONTEXT
    assert later.zone is not None
    assert later.zone.range_id != (frames[4].zone.range_id if frames[4].zone else None)
    assert frames[5].observation.observation_id == run(candles[:6])[5].observation.observation_id
