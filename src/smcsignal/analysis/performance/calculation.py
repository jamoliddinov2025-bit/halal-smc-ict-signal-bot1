"""Exact descriptive bucket arithmetic over published records. No reclassification."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking.calculation import aggregate
from smcsignal.analysis.outcome_tracking.models import (
    OutcomeSnapshot,
    OutcomeStatus,
    SignalOutcome,
)
from smcsignal.analysis.performance.config import PerformanceConfig
from smcsignal.analysis.performance.models import PerformanceBucket


def month_of(opened_at: datetime) -> str:
    """UTC calendar month of a signal candle, as the sortable YYYY-MM form."""

    if not isinstance(opened_at, datetime) or opened_at.tzinfo is None:
        raise AnalysisInputError("signal timestamps are timezone-aware UTC instants")
    moment = opened_at if opened_at.tzinfo is UTC else opened_at.astimezone(UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


def latest_outcomes(frames: tuple[OutcomeSnapshot, ...]) -> tuple[SignalOutcome, ...]:
    """Latest published version of each outcome, in creation order.

    Phase 18 publishes immutable per-version deltas; the newest version of an
    outcome is the last one a frame carried. Finalized versions are terminal.
    """

    versions: dict[str, SignalOutcome] = {}
    for frame in frames:
        for record in (*frame.created, *frame.evaluated, *frame.completed):
            versions[record.outcome_id] = record
    return tuple(versions.values())


def bucket_for(
    group: str,
    name: str,
    records: tuple[SignalOutcome, ...],
    *,
    total_buy_signals: int,
    open_count: int,
    settings: PerformanceConfig,
) -> PerformanceBucket:
    """One descriptive bucket recomputed with the Phase 18 aggregate helpers."""

    finalized = tuple(record for record in records if record.status is not OutcomeStatus.OPEN)
    summary = aggregate(
        finalized,
        total_buy_signals=total_buy_signals,
        open_count=open_count,
    )
    return PerformanceBucket(
        group=group,
        name=name,
        total_buy_signals=summary.total_buy_signals,
        open_count=summary.open_count,
        win_count=summary.win_count,
        loss_count=summary.loss_count,
        flat_count=summary.flat_count,
        finalized_count=summary.finalized_count,
        final_return_sum=summary.final_return_sum,
        mfe_return_sum=summary.mfe_return_sum,
        mae_return_sum=summary.mae_return_sum,
        win_rate=summary.win_rate,
        average_final_return=summary.average_final_return,
        average_mfe_return=summary.average_mfe_return,
        average_mae_return=summary.average_mae_return,
        sufficient_sample=summary.finalized_count >= settings.minimum_finalized_for_ranking,
    )


def _average(bucket: PerformanceBucket) -> Decimal:
    average = bucket.average_final_return
    if average is None:
        raise AnalysisInputError("a sufficient bucket always has finalized outcomes")
    return average


def rank_combinations(
    buckets: tuple[PerformanceBucket, ...],
) -> tuple[PerformanceBucket | None, PerformanceBucket | None]:
    """Best and worst sufficient combinations by average final return.

    Ties keep the first bucket in the deterministic sorted order. Groups below
    the configured minimum are never ranked, and their counts stay visible.
    """

    sufficient = [bucket for bucket in buckets if bucket.sufficient_sample]
    if not sufficient:
        return None, None
    best = max(sufficient, key=_average)
    worst = min(sufficient, key=_average)
    return best, worst
