"""Immutable descriptive performance facts over published records. Not advice."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.outcome_tracking.config import OutcomeTrackingConfig
from smcsignal.analysis.performance.config import PerformanceConfig
from smcsignal.analysis.setup_attribution.config import SetupAttributionConfig
from smcsignal.analysis.setup_attribution.models import SetupLabel

GROUPS = ("overall", "symbol", "timeframe", "label", "combination", "month")
NO_LABELS_KEY = "no_labels"


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")


def _counts(bucket: PerformanceBucket) -> None:
    counts = (
        bucket.total_buy_signals,
        bucket.open_count,
        bucket.win_count,
        bucket.loss_count,
        bucket.flat_count,
        bucket.finalized_count,
    )
    if any(type(count) is not int or count < 0 for count in counts):
        raise AnalysisInputError("performance counts must be nonnegative integers")
    if bucket.win_count + bucket.loss_count + bucket.flat_count != bucket.finalized_count:
        raise AnalysisInputError("win, loss, and flat counts must sum to finalized_count")
    if bucket.finalized_count + bucket.open_count != bucket.total_buy_signals:
        raise AnalysisInputError("finalized and open counts must sum to total_buy_signals")


@dataclass(frozen=True, slots=True)
class PerformanceBucket:
    """Descriptive statistics of one reporting group over published records.

    Counts are exact integers, return sums are exact Decimal additions of the
    Phase 18 descriptive ratios, and rates/averages are undefined (None)
    whenever no outcome is finalized. Open outcomes contribute only to the
    open count. sufficient_sample compares finalized outcomes against the
    configured ranking minimum; it never hides the raw counts. This is a
    software statistic of published signal records, not a backtest result,
    performance claim, or expected-return forecast.
    """

    group: str
    name: str
    total_buy_signals: int
    open_count: int
    win_count: int
    loss_count: int
    flat_count: int
    finalized_count: int
    final_return_sum: Decimal
    mfe_return_sum: Decimal
    mae_return_sum: Decimal
    win_rate: Decimal | None
    average_final_return: Decimal | None
    average_mfe_return: Decimal | None
    average_mae_return: Decimal | None
    sufficient_sample: bool

    def __post_init__(self) -> None:
        _text(self.group, "group")
        _text(self.name, "name")
        if self.group not in GROUPS:
            raise AnalysisInputError("group must be one of: " + ", ".join(GROUPS))
        _counts(self)
        sums = (self.final_return_sum, self.mfe_return_sum, self.mae_return_sum)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in sums):
            raise AnalysisInputError("performance sums must be finite Decimals")
        rates = (
            self.win_rate,
            self.average_final_return,
            self.average_mfe_return,
            self.average_mae_return,
        )
        if self.finalized_count == 0:
            if any(value is not None for value in rates):
                raise AnalysisInputError(
                    "rates and averages are undefined without finalized outcomes"
                )
        else:
            for value in rates:
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise AnalysisInputError(
                        "rates and averages must be finite Decimals when finalized exist"
                    )
            win_rate = self.win_rate
            if not isinstance(win_rate, Decimal):
                raise AnalysisInputError("win_rate must be a Decimal when outcomes are finalized")
            if not Decimal(0) <= win_rate <= Decimal(1):
                raise AnalysisInputError("win_rate must lie between zero and one")
        if type(self.sufficient_sample) is not bool:
            raise AnalysisInputError("sufficient_sample must be a boolean")


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """One deterministic multi-series descriptive report. Never advice.

    Buckets recompute from the latest published version of each Phase 18
    outcome record; statuses are copied, never reclassified. Label and
    combination buckets exist only for signals that carry a Phase 19b
    attribution profile. best/worst combinations are quoted only when a
    group reaches the configured minimum finalized count.
    """

    settings: PerformanceConfig
    outcome_settings: OutcomeTrackingConfig
    attribution_settings: SetupAttributionConfig | None
    overall: PerformanceBucket
    by_symbol: tuple[PerformanceBucket, ...]
    by_timeframe: tuple[PerformanceBucket, ...]
    by_label: tuple[PerformanceBucket, ...]
    by_combination: tuple[PerformanceBucket, ...]
    by_month: tuple[PerformanceBucket, ...]
    best_combination: PerformanceBucket | None
    worst_combination: PerformanceBucket | None
    series_keys: tuple[str, ...]
    report_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.settings, PerformanceConfig):
            raise AnalysisInputError("performance requires PerformanceConfig")
        if not isinstance(self.outcome_settings, OutcomeTrackingConfig):
            raise AnalysisInputError("performance requires OutcomeTrackingConfig")
        if self.attribution_settings is not None and not isinstance(
            self.attribution_settings, SetupAttributionConfig
        ):
            raise AnalysisInputError("attribution settings must be SetupAttributionConfig or None")
        if not isinstance(self.overall, PerformanceBucket):
            raise AnalysisInputError("performance requires an overall bucket")
        if self.overall.group != "overall" or self.overall.name != "all":
            raise AnalysisInputError("the overall bucket is the all-signals group")
        minimum = self.settings.minimum_finalized_for_ranking
        for group, buckets in (
            ("symbol", self.by_symbol),
            ("timeframe", self.by_timeframe),
            ("label", self.by_label),
            ("combination", self.by_combination),
            ("month", self.by_month),
        ):
            for bucket in buckets:
                if not isinstance(bucket, PerformanceBucket):
                    raise AnalysisInputError(f"{group} buckets must be PerformanceBucket")
                if bucket.group != group:
                    raise AnalysisInputError(f"a {group} bucket must declare its group")
                if bucket.sufficient_sample != (bucket.finalized_count >= minimum):
                    raise AnalysisInputError(
                        "sufficient_sample must match the configured ranking minimum"
                    )
        if [bucket.name for bucket in self.by_symbol] != sorted(
            bucket.name for bucket in self.by_symbol
        ):
            raise AnalysisInputError("symbol buckets must be sorted by name")
        if [bucket.name for bucket in self.by_timeframe] != sorted(
            bucket.name for bucket in self.by_timeframe
        ):
            raise AnalysisInputError("timeframe buckets must be sorted by name")
        if [bucket.name for bucket in self.by_combination] != sorted(
            bucket.name for bucket in self.by_combination
        ):
            raise AnalysisInputError("combination buckets must be sorted by name")
        if [bucket.name for bucket in self.by_month] != sorted(
            bucket.name for bucket in self.by_month
        ):
            raise AnalysisInputError("month buckets must be sorted by name")
        canonical = [label.value for label in SetupLabel]
        if [bucket.name for bucket in self.by_label] != [
            name for name in canonical if name in {bucket.name for bucket in self.by_label}
        ]:
            raise AnalysisInputError("label buckets must follow the canonical taxonomy order")
        for bucket in self.by_month:
            _month(bucket.name)
        for key in self.series_keys:
            _text(key, "series key")
        if list(self.series_keys) != sorted(self.series_keys):
            raise AnalysisInputError("series keys must be sorted")
        if not self.series_keys:
            raise AnalysisInputError("a report covers at least one series")
        if len(set(self.series_keys)) != len(self.series_keys):
            raise AnalysisInputError("series keys must be unique")
        _text(self.report_id, "report_id")
        if not self.report_id.startswith("performance-report:"):
            raise AnalysisInputError("report ids use the performance-report prefix")
        for name, buckets in (
            ("symbol", self.by_symbol),
            ("timeframe", self.by_timeframe),
            ("month", self.by_month),
        ):
            if not buckets:
                continue  # zero-signal reports may lack sub-buckets
            totals = sum(bucket.total_buy_signals for bucket in buckets)
            open_counts = sum(bucket.open_count for bucket in buckets)
            finalized = sum(bucket.finalized_count for bucket in buckets)
            if (totals, open_counts, finalized) != (
                self.overall.total_buy_signals,
                self.overall.open_count,
                self.overall.finalized_count,
            ):
                raise AnalysisInputError(
                    f"{name} buckets must partition the overall signal population"
                )
        sufficient = [bucket for bucket in self.by_combination if bucket.sufficient_sample]
        for ranked in (self.best_combination, self.worst_combination):
            if ranked is None:
                continue
            if ranked not in self.by_combination or not ranked.sufficient_sample:
                raise AnalysisInputError(
                    "ranked combinations must be sufficient combination buckets"
                )
        if sufficient:
            if self.best_combination is None or self.worst_combination is None:
                raise AnalysisInputError(
                    "sufficient combination buckets must be ranked best and worst"
                )
            averages = [
                value for bucket in sufficient if (value := bucket.average_final_return) is not None
            ]
            if len(averages) != len(sufficient):
                raise AnalysisInputError("a sufficient bucket always has finalized outcomes")
            if self.best_combination.average_final_return != max(averages):
                raise AnalysisInputError("the best combination has the top average")
            if self.worst_combination.average_final_return != min(averages):
                raise AnalysisInputError("the worst combination has the bottom average")
        elif self.best_combination is not None or self.worst_combination is not None:
            raise AnalysisInputError("rankings require at least one sufficient combination bucket")


def _month(name: str) -> None:
    _text(name, "month")
    if len(name) != 7 or name[4] != "-" or not name[:4].isdigit() or not name[5:].isdigit():
        raise AnalysisInputError("month buckets use the YYYY-MM UTC calendar form")
    if not 1 <= int(name[5:]) <= 12:
        raise AnalysisInputError("month buckets use the YYYY-MM UTC calendar form")
