"""Exact outcome arithmetic over published signals. No look-ahead, gates, or exits."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.displacement.calculation import difference, exact_sum, ratio
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.outcome_tracking.evidence import (
    outcome_identity,
    record_provenance,
)
from smcsignal.analysis.outcome_tracking.models import (
    AnalyticsSummary,
    OutcomeStatus,
    SignalOutcome,
    current_observation,
)
from smcsignal.analysis.signal_engine.models import SignalSnapshot, SignalStatus


def final_difference(final_close: Decimal, reference_close: Decimal) -> Decimal:
    """Exact final price difference; the only classification input."""

    return difference(final_close, reference_close)


def classify_outcome(outcome_difference: Decimal) -> OutcomeStatus:
    """Classify by the exact sign of the difference. No thresholds or fees."""

    if not isinstance(outcome_difference, Decimal) or not outcome_difference.is_finite():
        raise AnalysisInputError("classification requires a finite Decimal difference")
    if outcome_difference > 0:
        return OutcomeStatus.WIN
    if outcome_difference < 0:
        return OutcomeStatus.LOSS
    return OutcomeStatus.FLAT


def improved_high(
    prior_price: Decimal | None,
    prior_index: int | None,
    price: Decimal,
    index: int,
) -> tuple[Decimal, int]:
    """Running maximum high; an equal extreme keeps its first occurrence."""

    if (prior_price is None) != (prior_index is None):
        raise AnalysisInputError("prior extreme price and index must be present together")
    if prior_price is None or prior_index is None:
        return price, index
    if price > prior_price:
        return price, index
    return prior_price, prior_index


def improved_low(
    prior_price: Decimal | None,
    prior_index: int | None,
    price: Decimal,
    index: int,
) -> tuple[Decimal, int]:
    """Running minimum low; an equal extreme keeps its first occurrence."""

    if (prior_price is None) != (prior_index is None):
        raise AnalysisInputError("prior extreme price and index must be present together")
    if prior_price is None or prior_index is None:
        return price, index
    if price < prior_price:
        return price, index
    return prior_price, prior_index


def mfe_return(outcome: SignalOutcome) -> Decimal | None:
    """Descriptive maximum-favorable-excursion ratio; never a take-profit."""

    if outcome.mfe_price is None:
        return None
    return ratio(difference(outcome.mfe_price, outcome.reference_close), outcome.reference_close)


def mae_return(outcome: SignalOutcome) -> Decimal | None:
    """Descriptive maximum-adverse-excursion ratio; never a stop."""

    if outcome.mae_price is None:
        return None
    return ratio(difference(outcome.mae_price, outcome.reference_close), outcome.reference_close)


def final_return(outcome: SignalOutcome) -> Decimal | None:
    """Descriptive final return ratio over the fixed horizon."""

    if outcome.final_close is None:
        return None
    return ratio(difference(outcome.final_close, outcome.reference_close), outcome.reference_close)


def aggregate(
    finalized: tuple[SignalOutcome, ...], *, total_buy_signals: int, open_count: int
) -> AnalyticsSummary:
    """Pure aggregate over finalized outcomes in creation order.

    Counts are exact, sums are exact Decimal additions of descriptive per-outcome
    ratios, and rates/averages are descriptive ratios. Undefined statistics are
    None, never zero. This is not a backtest or performance claim.
    """

    if type(total_buy_signals) is not int or total_buy_signals < 0:
        raise AnalysisInputError("total_buy_signals must be a nonnegative integer")
    if type(open_count) is not int or open_count < 0:
        raise AnalysisInputError("open_count must be a nonnegative integer")
    for record in finalized:
        if record.status is OutcomeStatus.OPEN:
            raise AnalysisInputError("aggregates include finalized outcomes only")
    if total_buy_signals != open_count + len(finalized):
        raise AnalysisInputError("open and finalized counts must sum to total_buy_signals")
    wins = sum(record.status is OutcomeStatus.WIN for record in finalized)
    losses = sum(record.status is OutcomeStatus.LOSS for record in finalized)
    flats = sum(record.status is OutcomeStatus.FLAT for record in finalized)
    final_sum = exact_sum(tuple(value for value in (final_return(r) for r in finalized) if value))
    mfe_sum = exact_sum(tuple(value for value in (mfe_return(r) for r in finalized) if value))
    mae_sum = exact_sum(tuple(value for value in (mae_return(r) for r in finalized) if value))
    count = Decimal(len(finalized))
    if finalized:
        return AnalyticsSummary(
            total_buy_signals=total_buy_signals,
            open_count=open_count,
            win_count=wins,
            loss_count=losses,
            flat_count=flats,
            finalized_count=len(finalized),
            final_return_sum=final_sum,
            mfe_return_sum=mfe_sum,
            mae_return_sum=mae_sum,
            win_rate=ratio(Decimal(wins), count),
            average_final_return=ratio(final_sum, count),
            average_mfe_return=ratio(mfe_sum, count),
            average_mae_return=ratio(mae_sum, count),
        )
    return AnalyticsSummary(
        total_buy_signals=total_buy_signals,
        open_count=open_count,
        win_count=0,
        loss_count=0,
        flat_count=0,
        finalized_count=0,
        final_return_sum=final_sum,
        mfe_return_sum=mfe_sum,
        mae_return_sum=mae_sum,
        win_rate=None,
        average_final_return=None,
        average_mfe_return=None,
        average_mae_return=None,
    )


def open_outcome(
    frame: SignalSnapshot, settings: OutcomeTrackingConfig, config_hash: str
) -> SignalOutcome:
    """Create the initial open version for one BUY_SIGNAL frame. Stateless mapping."""

    if not isinstance(frame, SignalSnapshot):
        raise AnalysisInputError("outcome tracking consumes existing SignalSnapshot frames")
    if not isinstance(settings, OutcomeTrackingConfig):
        raise AnalysisInputError("settings must be OutcomeTrackingConfig")
    if frame.status is not SignalStatus.BUY_SIGNAL:
        raise AnalysisInputError("outcomes are created from BUY_SIGNAL frames only")
    observation = current_observation(frame)
    identity = outcome_identity(frame, settings)
    key = {
        "signal_id": frame.signal_id,
        "setup_identity": frame.setup_identity,
        "status": OutcomeStatus.OPEN,
        "candles_observed": 0,
        "reference": observation.reference,
        "reference_close": observation.candle.close,
        "mfe": None,
        "mae": None,
        "final": None,
        "created_available_at": observation.available_at,
        "evaluated_available_at": None,
        "finalized_available_at": None,
    }
    provenance = record_provenance(
        frame,
        settings,
        identity,
        key,
        available_at=observation.available_at,
        source_candles=(observation.reference,),
        dependencies=(frame.provenance.as_reference(),),
        config_hash=config_hash,
    )
    return SignalOutcome(
        settings=settings,
        status=OutcomeStatus.OPEN,
        outcome_id=identity,
        signal_id=frame.signal_id,
        setup_identity=frame.setup_identity,
        symbol=observation.reference.series.symbol,
        timeframe=observation.reference.series.timeframe,
        horizon_bars=settings.horizon_bars,
        reference=observation.reference,
        reference_close=observation.candle.close,
        candles_observed=0,
        mfe_price=None,
        mfe_index=None,
        mae_price=None,
        mae_index=None,
        final_index=None,
        final_close=None,
        created_available_at=observation.available_at,
        evaluated_available_at=None,
        finalized_available_at=None,
        provenance=provenance,
    )


def advance_outcome(
    prior: SignalOutcome, frame: SignalSnapshot, settings: OutcomeTrackingConfig, config_hash: str
) -> SignalOutcome:
    """Evaluate one open outcome against the current frame's closed candle.

    Extremes always update before the horizon check; the finalizing version
    carries the finals and never changes again. Stateless per-version mapping.
    """

    if not isinstance(prior, SignalOutcome):
        raise AnalysisInputError("advance requires an existing SignalOutcome version")
    if not isinstance(frame, SignalSnapshot):
        raise AnalysisInputError("outcome tracking consumes existing SignalSnapshot frames")
    if not isinstance(settings, OutcomeTrackingConfig):
        raise AnalysisInputError("settings must be OutcomeTrackingConfig")
    if prior.status is not OutcomeStatus.OPEN:
        raise AnalysisInputError("only open outcomes are evaluated")
    if prior.settings != settings:
        raise AnalysisInputError("advance must use the outcome's exact configuration")
    observation = current_observation(frame)
    if observation.reference.candle_index <= prior.reference.candle_index:
        raise AnalysisInputError("evaluation candles must follow the signal candle")
    observed = prior.candles_observed + 1
    if observed > settings.horizon_bars:
        raise AnalysisInputError("a finalized outcome receives no further evaluation")
    mfe_price, mfe_index = improved_high(
        prior.mfe_price,
        prior.mfe_index,
        observation.candle.high,
        observation.reference.candle_index,
    )
    mae_price, mae_index = improved_low(
        prior.mae_price, prior.mae_index, observation.candle.low, observation.reference.candle_index
    )
    status = OutcomeStatus.OPEN
    final_index: int | None = None
    final_close: Decimal | None = None
    finalized_available_at: datetime | None = None
    if observed == settings.horizon_bars:
        status = classify_outcome(final_difference(observation.candle.close, prior.reference_close))
        final_index = observation.reference.candle_index
        final_close = observation.candle.close
        finalized_available_at = observation.available_at
    key = {
        "signal_id": prior.signal_id,
        "setup_identity": prior.setup_identity,
        "status": status,
        "candles_observed": observed,
        "reference": prior.reference,
        "reference_close": prior.reference_close,
        "mfe": {"price": mfe_price, "index": mfe_index},
        "mae": {"price": mae_price, "index": mae_index},
        "final": (
            None
            if final_close is None or final_index is None
            else {"close": final_close, "index": final_index}
        ),
        "created_available_at": prior.created_available_at,
        "evaluated_available_at": observation.available_at,
        "finalized_available_at": finalized_available_at,
    }
    provenance = record_provenance(
        frame,
        settings,
        prior.outcome_id,
        key,
        available_at=observation.available_at,
        source_candles=(
            prior.reference,
            observation.reference,
        ),
        dependencies=(
            frame.provenance.as_reference(),
            prior.provenance.as_reference(),
        ),
        config_hash=config_hash,
    )
    return SignalOutcome(
        settings=settings,
        status=status,
        outcome_id=prior.outcome_id,
        signal_id=prior.signal_id,
        setup_identity=prior.setup_identity,
        symbol=prior.symbol,
        timeframe=prior.timeframe,
        horizon_bars=settings.horizon_bars,
        reference=prior.reference,
        reference_close=prior.reference_close,
        candles_observed=observed,
        mfe_price=mfe_price,
        mfe_index=mfe_index,
        mae_price=mae_price,
        mae_index=mae_index,
        final_index=final_index,
        final_close=final_close,
        created_available_at=prior.created_available_at,
        evaluated_available_at=observation.available_at,
        finalized_available_at=finalized_available_at,
        provenance=provenance,
    )
