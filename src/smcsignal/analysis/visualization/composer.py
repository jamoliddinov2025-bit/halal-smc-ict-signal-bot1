"""Compose a drawing from already-published frames. No detection runs here."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.fvg.models import FVGSnapshot
from smcsignal.analysis.indicators.models import IndicatorSnapshot
from smcsignal.analysis.liquidity.models import ObservedCandle
from smcsignal.analysis.models import TrendDirection
from smcsignal.analysis.ote.calculation import OTEDirection
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.outcome_tracking.models import (
    OutcomeSnapshot,
    OutcomeStatus,
)
from smcsignal.analysis.signal_engine.models import (
    SignalSnapshot,
    SignalStatus,
)
from smcsignal.analysis.signal_engine.models import (
    current_observation as signal_observation,
)
from smcsignal.analysis.visualization.config import VisualizationConfig
from smcsignal.analysis.visualization.models import (
    DrawingModel,
    EventMarker,
    IndicatorOverlay,
    LevelLine,
    Primitive,
    StyleToken,
    TextAnnotation,
    ZoneRect,
    canonical_order,
)

_PRICE_TOKENS = {
    TrendDirection.BULLISH: StyleToken.BULLISH,
    TrendDirection.BEARISH: StyleToken.BEARISH,
}

_OUTCOME_TOKENS = {
    OutcomeStatus.WIN: StyleToken.BULLISH,
    OutcomeStatus.LOSS: StyleToken.BEARISH,
    OutcomeStatus.FLAT: StyleToken.NEUTRAL,
}


def _token(direction: TrendDirection) -> StyleToken:
    token = _PRICE_TOKENS.get(direction)
    if token is None:
        raise AnalysisInputError("drawing directions are strictly bullish or bearish")
    return token


def _validate_primary(frames: tuple[OTESnapshot, ...]) -> None:
    if not isinstance(frames, tuple) or not frames:
        raise AnalysisInputError("a drawing requires a nonempty primary replay")
    for position, frame in enumerate(frames):
        if not isinstance(frame, OTESnapshot):
            raise AnalysisInputError("the primary replay must contain OTESnapshot frames")
        if frame.observation.evaluation.reference.candle_index != position:
            raise AnalysisInputError("primary frames must run from zero consecutively")
        if frame.provenance.series != frames[0].provenance.series:
            raise AnalysisInputError("the primary series cannot change mid-replay")
        if frame.settings != frames[0].settings:
            raise AnalysisInputError("the primary replay must use one configuration")


def _observation_of(
    frame: SignalSnapshot | IndicatorSnapshot | OutcomeSnapshot,
) -> ObservedCandle:
    if isinstance(frame, SignalSnapshot):
        return signal_observation(frame.upstream)
    if isinstance(frame, OutcomeSnapshot):
        return signal_observation(frame.upstream.upstream)
    if isinstance(frame, IndicatorSnapshot):
        return frame.upstream.metrics.observation
    raise AnalysisInputError("unsupported aligned frame")


def _validate_aligned(
    name: str,
    frames: (
        tuple[SignalSnapshot, ...] | tuple[IndicatorSnapshot, ...] | tuple[OutcomeSnapshot, ...]
    ),
    kind: type[SignalSnapshot] | type[IndicatorSnapshot] | type[OutcomeSnapshot],
    primary: tuple[OTESnapshot, ...],
) -> None:
    if not isinstance(frames, tuple) or not frames:
        raise AnalysisInputError(f"{name} must be a nonempty tuple of frames")
    if len(frames) != len(primary):
        raise AnalysisInputError(f"{name} must align with the primary replay")
    for frame, reference_frame in zip(frames, primary, strict=True):
        if not isinstance(frame, kind):
            raise AnalysisInputError(f"{name} must contain {kind.__name__} frames")
        if frame.provenance.series != primary[0].provenance.series:
            raise AnalysisInputError(f"{name} must replay the same series")
        observed = _observation_of(frame)
        if observed.reference != reference_frame.observation.evaluation.reference:
            raise AnalysisInputError(f"{name} must replay the same candles")


def compose_drawing(
    primary: tuple[OTESnapshot, ...],
    signals: tuple[SignalSnapshot, ...] | None = None,
    indicators: tuple[IndicatorSnapshot, ...] | None = None,
    outcomes: tuple[OutcomeSnapshot, ...] | None = None,
    config: VisualizationConfig | None = None,
) -> DrawingModel:
    """Project published facts onto drawing primitives, in canonical order.

    The composer reads only facts that already exist on the consumed frames:
    FVG zones, order-block zones, displacement events, sweeps, liquidity pool
    levels, dealing ranges, equilibrium, OTE zones, BUY markers, finalized
    outcome markers, and price-scaled indicator overlays. It never reruns a
    detector and never invents a fact. RSI and volume ratios are not price
    anchored and are not drawn.
    """

    if config is not None and not isinstance(config, VisualizationConfig):
        raise AnalysisInputError("config must be VisualizationConfig")
    settings = config if config is not None else VisualizationConfig()
    _validate_primary(primary)
    if signals is not None:
        _validate_aligned("signals", signals, SignalSnapshot, primary)
    if indicators is not None:
        _validate_aligned("indicators", indicators, IndicatorSnapshot, primary)
    if outcomes is not None:
        _validate_aligned("outcomes", outcomes, OutcomeSnapshot, primary)
    primitives: list[Primitive] = []
    seen_pools: set[str] = set()
    for frame in primary:
        position = frame.observation.evaluation.reference.candle_index
        displacement = frame.upstream.upstream.upstream.upstream
        liquidity = displacement.liquidity
        for pool in liquidity.pool_updates:
            if pool.pool_id in seen_pools:
                continue  # later versions of one pool draw one level
            seen_pools.add(pool.pool_id)
            primitives.append(
                LevelLine(
                    price=pool.reference_price,
                    label=f"liquidity {pool.kind.value}",
                    token=StyleToken.NEUTRAL,
                    start_index=position,
                )
            )
        for sweep in liquidity.sweeps:
            primitives.append(
                EventMarker(
                    index=sweep.breach.reference.candle_index,
                    price=sweep.extreme_price,
                    label=f"liquidity sweep {sweep.side.value}",
                    token=StyleToken.NEUTRAL,
                )
            )
        for event in displacement.events:
            primitives.append(
                EventMarker(
                    index=event.detection_index,
                    price=event.metrics.observation.candle.close,
                    label=f"displacement {event.direction.value}",
                    token=_token(event.direction),
                )
            )
        gaps: FVGSnapshot = frame.upstream.upstream.upstream
        for gap_event in gaps.events:
            primitives.append(
                ZoneRect(
                    lower=gap_event.lower_boundary,
                    upper=gap_event.upper_boundary,
                    start_index=gap_event.creation_index,
                    end_index=None,
                    label=f"fvg {gap_event.direction.value}",
                    token=_token(gap_event.direction),
                )
            )
        blocks = frame.upstream.upstream
        for block_event in blocks.events:
            primitives.append(
                ZoneRect(
                    lower=block_event.zone_lower_boundary,
                    upper=block_event.zone_upper_boundary,
                    start_index=block_event.candidate_index,
                    end_index=None,
                    label=f"order block {block_event.direction.value}",
                    token=_token(block_event.direction),
                )
            )
        premium_discount = frame.upstream
        dealing_range = premium_discount.dealing_range
        if dealing_range is not None and dealing_range.confirmation_index == position:
            primitives.append(
                ZoneRect(
                    lower=dealing_range.lower_boundary,
                    upper=dealing_range.upper_boundary,
                    start_index=dealing_range.start_index,
                    end_index=dealing_range.end_index,
                    label="dealing range",
                    token=StyleToken.REFERENCE,
                )
            )
            if premium_discount.equilibrium is not None:
                primitives.append(
                    LevelLine(
                        price=premium_discount.equilibrium.midpoint,
                        label="equilibrium",
                        token=StyleToken.REFERENCE,
                        start_index=dealing_range.confirmation_index,
                    )
                )
        zone = frame.zone
        if zone is not None and zone.confirmation_index == position:
            primitives.append(
                ZoneRect(
                    lower=zone.lower_boundary,
                    upper=zone.upper_boundary,
                    start_index=zone.confirmation_index,
                    end_index=None,
                    label=f"ote zone {zone.direction.value}",
                    token=StyleToken.BULLISH
                    if zone.direction is OTEDirection.BULLISH
                    else StyleToken.BEARISH,
                )
            )
    if signals is not None:
        for signal_frame in signals:
            if signal_frame.status is not SignalStatus.BUY_SIGNAL:
                continue
            observation = signal_observation(signal_frame.upstream)
            close = observation.candle.close
            primitives.append(
                EventMarker(
                    index=observation.reference.candle_index,
                    price=close,
                    label="buy signal",
                    token=StyleToken.BULLISH,
                )
            )
            primitives.append(
                TextAnnotation(
                    index=observation.reference.candle_index,
                    price=close,
                    text=f"score {signal_frame.candidate.signal.score_total}",
                    token=StyleToken.BULLISH,
                )
            )
    if outcomes is not None:
        for outcome_frame in outcomes:
            # Completed versions are terminal facts published at their frame;
            # open outcomes draw nothing beyond the existing BUY marker.
            for record in outcome_frame.completed:
                final_index = record.final_index
                final_close = record.final_close
                if final_index is None or final_close is None:
                    raise AnalysisInputError("a completed outcome carries final index and close")
                token = _OUTCOME_TOKENS.get(record.status)
                if token is None:
                    raise AnalysisInputError("outcome markers exist only for finalized outcomes")
                primitives.append(
                    EventMarker(
                        index=final_index,
                        price=final_close,
                        label=f"outcome {record.status.value}",
                        token=token,
                    )
                )
    if indicators is not None:
        for indicator_frame in indicators:
            for period, value in zip(
                indicator_frame.settings.ema_periods,
                indicator_frame.ema_values,
                strict=True,
            ):
                if value is None:
                    continue
                primitives.append(
                    IndicatorOverlay(
                        index=indicator_frame.candle_index,
                        label=f"ema{period}",
                        value=value,
                    )
                )
            if indicator_frame.atr is not None:
                primitives.append(
                    IndicatorOverlay(
                        index=indicator_frame.candle_index,
                        label="atr",
                        value=indicator_frame.atr,
                    )
                )
    series = primary[0].provenance.series
    return DrawingModel(
        settings=settings,
        symbol=series.symbol,
        timeframe=series.timeframe,
        candles=tuple(frame.observation.evaluation for frame in primary),
        primitives=canonical_order(tuple(primitives)),
    )


def price_domain(model: DrawingModel) -> tuple[Decimal, Decimal]:
    """The exact [low, high] price domain covered by candles and primitives."""

    lows = [observation.candle.low for observation in model.candles]
    highs = [observation.candle.high for observation in model.candles]
    for primitive in model.primitives:
        if isinstance(primitive, LevelLine):
            lows.append(primitive.price)
            highs.append(primitive.price)
        elif isinstance(primitive, ZoneRect):
            lows.append(primitive.lower)
            highs.append(primitive.upper)
        elif isinstance(primitive, EventMarker):
            lows.append(primitive.price)
            highs.append(primitive.price)
    low, high = min(lows), max(highs)
    if low == high:
        low, high = low - Decimal(1), high + Decimal(1)
    return low, high
