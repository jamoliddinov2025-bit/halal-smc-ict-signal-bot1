"""Pure row extraction over existing Phase 17–19 records; no recomputation."""

from __future__ import annotations

from smcsignal.analysis.backtest.models import (
    BacktestConfiguration,
    BacktestSignalResult,
    ReplayDataset,
    ReplayResult,
)
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.mtf.config import MTFConfig
from smcsignal.analysis.outcome_tracking.calculation import (
    final_return,
    mae_return,
    mfe_return,
)
from smcsignal.analysis.outcome_tracking.models import OutcomeSnapshot
from smcsignal.analysis.performance.calculation import latest_outcomes
from smcsignal.analysis.setup_attribution.models import (
    AttributionSnapshot,
    SetupAttribution,
)


def series_key_for(dataset: ReplayDataset) -> str:
    """Deterministic unique key of one replayed dataset."""

    return (
        f"{dataset.symbol}:{dataset.timeframe}:{dataset.venue}:"
        f"{dataset.provider}:{dataset.dataset_id}"
    )


def effective_mtf_config(configuration: BacktestConfiguration, dataset: ReplayDataset) -> MTFConfig:
    """The dataset-driven MTF configuration; higher timeframes are unchanged.

    One backtest configuration serves datasets on several primary timeframes;
    each replay derives its MTF configuration from the dataset's declared
    timeframe so the multiples, ordering, and availability policy stay
    validated by the existing strict MTFConfig constructor.
    """

    if not isinstance(configuration, BacktestConfiguration):
        raise AnalysisInputError("effective MTF configuration requires a BacktestConfiguration")
    if not isinstance(dataset, ReplayDataset):
        raise AnalysisInputError("effective MTF configuration requires a ReplayDataset")
    return MTFConfig(
        primary_timeframe=dataset.timeframe,
        higher_timeframes=configuration.mtf.higher_timeframes,
        availability_policy=configuration.mtf.availability_policy,
    )


def _profiles(frames: tuple[AttributionSnapshot, ...]) -> dict[str, SetupAttribution]:
    profiles: dict[str, SetupAttribution] = {}
    for snapshot in frames:
        if snapshot.attribution is not None:
            profiles[snapshot.attribution.signal_id] = snapshot.attribution
    return profiles


def signal_rows(
    dataset_key: str,
    outcomes: tuple[OutcomeSnapshot, ...],
    attributions: tuple[AttributionSnapshot, ...],
) -> tuple[BacktestSignalResult, ...]:
    """One row per published BUY_SIGNAL, sorted by opened_at then signal_id.

    Values are copied from the latest published Phase 18 outcome version and
    the Phase 19b attribution profile; return ratios use the existing
    Phase 18 helpers. Nothing is classified or aggregated here.
    """

    if not isinstance(outcomes, tuple) or not isinstance(attributions, tuple):
        raise AnalysisInputError("rows require existing outcome and attribution frames")
    if len(outcomes) != len(attributions):
        raise AnalysisInputError("attribution frames must align with outcome frames")
    for frame, attribution in zip(outcomes, attributions, strict=True):
        if attribution.upstream is not frame.upstream:
            raise AnalysisInputError("attribution frames must replay the same signal frames")
    profiles = _profiles(attributions)
    directions = {frame.upstream.signal_id: frame.upstream.direction for frame in outcomes}
    rows: list[BacktestSignalResult] = []
    for record in latest_outcomes(outcomes):
        profile = profiles.get(record.signal_id)
        if profile is None:
            raise AnalysisInputError("every BUY_SIGNAL carries one attribution profile")
        direction = directions.get(record.signal_id)
        if direction is None:
            raise AnalysisInputError("every outcome record references a published signal frame")
        rows.append(
            BacktestSignalResult(
                dataset_key=dataset_key,
                signal_id=record.signal_id,
                setup_identity=record.setup_identity,
                symbol=record.symbol,
                timeframe=record.timeframe,
                candle_index=record.reference.candle_index,
                opened_at=record.reference.opened_at,
                closed_at=record.reference.closed_at,
                direction=direction,
                score_total=profile.score_total,
                labels=profile.labels,
                combination_key=profile.combination_key,
                outcome_id=record.outcome_id,
                outcome_status=record.status,
                reference_close=record.reference_close,
                final_index=record.final_index,
                final_close=record.final_close,
                final_return=final_return(record),
                mfe_return=mfe_return(record),
                mae_return=mae_return(record),
                outcome=record,
            )
        )
    rows.sort(key=lambda row: (row.opened_at, row.signal_id))
    return tuple(rows)


def replay_rows(replay: ReplayResult) -> tuple[BacktestSignalResult, ...]:
    """Rows of one finished replay under its dataset key."""

    if not isinstance(replay, ReplayResult):
        raise AnalysisInputError("replay rows require a ReplayResult")
    return signal_rows(series_key_for(replay.dataset), replay.outcomes, replay.attributions)
