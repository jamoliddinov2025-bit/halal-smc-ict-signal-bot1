"""Build one deterministic descriptive performance report over finished replays."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.outcome_tracking.models import (
    OutcomeSnapshot,
    OutcomeStatus,
    SignalOutcome,
)
from smcsignal.analysis.performance.calculation import (
    bucket_for,
    latest_outcomes,
    month_of,
    rank_combinations,
)
from smcsignal.analysis.performance.config import PerformanceConfig
from smcsignal.analysis.performance.evidence import (
    configuration_artifact,
    report_identity,
)
from smcsignal.analysis.performance.models import (
    NO_LABELS_KEY,
    PerformanceBucket,
    PerformanceReport,
)
from smcsignal.analysis.setup_attribution.config import SetupAttributionConfig
from smcsignal.analysis.setup_attribution.models import (
    AttributionSnapshot,
    SetupAttribution,
    SetupLabel,
)


def _series_frames(
    label: str,
    frames: tuple[OutcomeSnapshot, ...],
    outcome_settings: OutcomeTrackingConfig | None,
) -> tuple[OutcomeTrackingConfig, str, str]:
    """Validate one replay and read its series symbol and timeframe."""

    if not isinstance(frames, tuple) or not frames:
        raise AnalysisInputError("each series must contribute a nonempty tuple of outcome frames")
    for position, frame in enumerate(frames):
        if not isinstance(frame, OutcomeSnapshot):
            raise AnalysisInputError(f"series {label!r} must contain OutcomeSnapshot frames")
        if frame.upstream.candidate.signal.candle.candle_index != position:
            raise AnalysisInputError(
                f"series {label!r} frames must start at zero in consecutive order"
            )
        if frame.settings != frames[0].settings:
            raise AnalysisInputError(
                f"series {label!r} must use one outcome tracking configuration"
            )
        if frame.provenance.series != frames[0].provenance.series:
            raise AnalysisInputError(f"series {label!r} cannot change mid-replay")
    first = frames[0]
    if outcome_settings is not None and first.settings != outcome_settings:
        raise AnalysisInputError("every series must use one outcome tracking configuration")
    series = first.provenance.series
    return first.settings, series.symbol, series.timeframe


def _attribution_frames(
    label: str,
    frames: tuple[AttributionSnapshot, ...],
    outcomes: tuple[OutcomeSnapshot, ...],
    attribution_settings: SetupAttributionConfig | None,
) -> SetupAttributionConfig:
    """Validate one attribution replay and pair it with its outcome frames."""

    if not isinstance(frames, tuple) or not frames:
        raise AnalysisInputError(f"attribution series {label!r} must be a nonempty tuple of frames")
    if len(frames) != len(outcomes):
        raise AnalysisInputError(f"attribution series {label!r} must align with its outcome frames")
    for frame, outcome in zip(frames, outcomes, strict=True):
        if not isinstance(frame, AttributionSnapshot):
            raise AnalysisInputError(
                f"attribution series {label!r} must contain AttributionSnapshot frames"
            )
        if frame.upstream is not outcome.upstream:
            raise AnalysisInputError(
                f"attribution series {label!r} must replay the same signal frames"
            )
        if frame.settings != frames[0].settings:
            raise AnalysisInputError(
                f"attribution series {label!r} must use one attribution configuration"
            )
    settings = frames[0].settings
    if attribution_settings is not None and settings != attribution_settings:
        raise AnalysisInputError("every series must use one attribution configuration")
    return settings


def _buckets_by_name(
    group: str,
    grouped: dict[str, list[SignalOutcome]],
    settings: PerformanceConfig,
) -> tuple[PerformanceBucket, ...]:
    buckets = []
    for name in sorted(grouped):
        records = tuple(grouped[name])
        open_count = sum(record.status is OutcomeStatus.OPEN for record in records)
        buckets.append(
            bucket_for(
                group,
                name,
                records,
                total_buy_signals=len(records),
                open_count=open_count,
                settings=settings,
            )
        )
    return tuple(buckets)


def _label_buckets(
    grouped: dict[SetupLabel, list[SignalOutcome]],
    settings: PerformanceConfig,
) -> tuple[PerformanceBucket, ...]:
    buckets = []
    for label in SetupLabel:
        if label not in grouped:
            continue
        records = tuple(grouped[label])
        open_count = sum(record.status is OutcomeStatus.OPEN for record in records)
        buckets.append(
            bucket_for(
                "label",
                label.value,
                records,
                total_buy_signals=len(records),
                open_count=open_count,
                settings=settings,
            )
        )
    return tuple(buckets)


def analyze_performance(
    outcomes: Mapping[str, tuple[OutcomeSnapshot, ...]],
    attributions: Mapping[str, tuple[AttributionSnapshot, ...]] | None = None,
    config: PerformanceConfig | None = None,
) -> PerformanceReport:
    """Recompute exact descriptive statistics over finished series replays.

    Series keys are caller-supplied and sorted deterministically. Outcome
    statuses are copied from the latest published Phase 18 versions; nothing
    is reclassified. Open and finalized outcomes stay strictly separated.
    Label and combination buckets exist only for attributed signals.
    """

    if not isinstance(outcomes, Mapping):
        raise AnalysisInputError("outcomes must be a mapping of series keys to frames")
    if attributions is not None and not isinstance(attributions, Mapping):
        raise AnalysisInputError("attributions must be a mapping of series keys to frames")
    if config is not None and not isinstance(config, PerformanceConfig):
        raise AnalysisInputError("config must be PerformanceConfig")
    settings = config if config is not None else PerformanceConfig()
    if not outcomes:
        raise AnalysisInputError("performance requires at least one series replay")
    keys = sorted(outcomes)
    for key in keys:
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise AnalysisInputError("series keys must be nonempty, trimmed strings")
    if attributions is not None:
        unknown = sorted(set(attributions) - set(outcomes))
        if unknown:
            raise AnalysisInputError(
                "attribution series without outcomes are forbidden: " + ", ".join(unknown)
            )
    outcome_settings: OutcomeTrackingConfig | None = None
    attribution_settings: SetupAttributionConfig | None = None
    series_payload: list[dict[str, str | None]] = []
    symbols: dict[str, list[SignalOutcome]] = {}
    timeframes: dict[str, list[SignalOutcome]] = {}
    months: dict[str, list[SignalOutcome]] = {}
    labels: dict[SetupLabel, list[SignalOutcome]] = {}
    combinations: dict[str, list[SignalOutcome]] = {}
    all_signals: list[SignalOutcome] = []
    seen_signal_ids: set[str] = set()
    for key in keys:
        frames = outcomes[key]
        outcome_settings, symbol, timeframe = _series_frames(key, frames, outcome_settings)
        attribution_frames: tuple[AttributionSnapshot, ...] | None = None
        if attributions is not None and key in attributions:
            attribution_settings = _attribution_frames(
                key, attributions[key], frames, attribution_settings
            )
            attribution_frames = attributions[key]
        records = latest_outcomes(frames)
        profiles: dict[str, SetupAttribution] = {}
        if attribution_frames is not None:
            for snapshot in attribution_frames:
                if snapshot.attribution is not None:
                    profiles[snapshot.attribution.signal_id] = snapshot.attribution
        for record in records:
            if record.signal_id in seen_signal_ids:
                raise AnalysisInputError("a signal belongs to exactly one series")
            seen_signal_ids.add(record.signal_id)
            if record.symbol != symbol or record.timeframe != timeframe:
                raise AnalysisInputError(
                    f"series {key!r} records must match the replay symbol and timeframe"
                )
            all_signals.append(record)
            symbols.setdefault(symbol, []).append(record)
            timeframes.setdefault(timeframe, []).append(record)
            months.setdefault(month_of(record.reference.opened_at), []).append(record)
            profile = profiles.get(record.signal_id)
            attribution_evidence = None
            if profile is not None:
                attribution_evidence = profile.provenance.evidence_id
                for label in profile.labels:
                    labels.setdefault(label, []).append(record)
                combinations.setdefault(profile.combination_key or NO_LABELS_KEY, []).append(record)
            series_payload.append(
                {
                    "signal_id": record.signal_id,
                    "outcome": record.provenance.evidence_id,
                    "attribution": attribution_evidence,
                }
            )

    assert outcome_settings is not None
    finalized = tuple(record for record in all_signals if record.status is not OutcomeStatus.OPEN)
    overall = bucket_for(
        "overall",
        "all",
        tuple(all_signals),
        total_buy_signals=len(all_signals),
        open_count=len(all_signals) - len(finalized),
        settings=settings,
    )
    by_symbol = _buckets_by_name("symbol", symbols, settings)
    by_timeframe = _buckets_by_name("timeframe", timeframes, settings)
    by_label = _label_buckets(labels, settings)
    by_combination = _buckets_by_name("combination", combinations, settings)
    by_month = _buckets_by_name("month", months, settings)
    best, worst = rank_combinations(by_combination)
    report_id = report_identity(
        settings,
        outcome_settings,
        attribution_settings,
        series_payload,
    )
    return PerformanceReport(
        settings=settings,
        outcome_settings=outcome_settings,
        attribution_settings=attribution_settings,
        overall=overall,
        by_symbol=by_symbol,
        by_timeframe=by_timeframe,
        by_label=by_label,
        by_combination=by_combination,
        by_month=by_month,
        best_combination=best,
        worst_combination=worst,
        series_keys=tuple(keys),
        report_id=report_id,
    )


def configuration_hash(config: PerformanceConfig) -> str:
    """SHA-256 of the frozen methodology artifact."""

    return sha256(configuration_artifact(config)).hexdigest()
