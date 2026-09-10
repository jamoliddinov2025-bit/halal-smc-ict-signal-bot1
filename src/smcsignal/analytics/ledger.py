"""Phase 26C: deterministic analytics-ledger snapshot and restore. BYTES ONLY.

The outcome lifecycle (Phases 26A/26B) keeps the publication ledger in memory.
Phase 26C turns that ledger into canonical, content-addressed bytes and back,
so a later approved phase can persist those bytes without this module ever
performing IO. Nothing here writes a file, opens a connection, reads a clock,
or reaches a network: ``ledger_bytes`` returns bytes and ``load_ledger_bytes``
accepts bytes; storage is a future phase's job.

Serialization canon (LOCK 2)
----------------------------

This module invents no serialization convention. Bytes are produced by the
repository's existing evidence canon (``smcsignal.analysis.liquidity.evidence``:
sorted compact JSON, exact ``Decimal`` text, UTC ISO-8601 timestamps, enum
values, frozen-dataclass field mapping) — the same canon that identities and
provenance have used since Phase 3. Restoration is the explicit mirror of the
frozen model field sets, and every reconstructed value passes through the
actual frozen constructors (``SeriesProvenance``, ``CandleReference``,
``EvidenceReference``, ``EvidenceProvenance``, ``OutcomeTrackingConfig``,
Phase 18 ``SignalOutcome``, Phase 26A ``SignalObservation``), so load-time
validation *is* the frozen validation — nothing is re-implemented here.

Integrity (LOCK 5)
------------------

``snapshot_id`` is the content-addressed digest of the snapshot contents and
is embedded in the serialized document. On load the digest is always
recomputed over the parsed contents and compared with the embedded value: a
supplied digest is never trusted, and any tampering that changes the content
either fails schema/model validation or produces a digest mismatch.

Restored ledgers are read-only (LOCK 4)
---------------------------------------

``RestoredAnalyticsLedger`` represents historical facts only. It exposes the
same read views as the live observer (observations, open outcomes, finalized
outcomes, ``StrategyStats``, UTC ``MonthlyReport``) computed through the same
frozen Phase 18 ``aggregate()`` machinery, and nothing else: it accepts no new
observations, no new finals, no frames, and it resumes no evaluator.
Evaluator continuation after a restart (re-feed versus checkpointing) is a
future phase by design; the Phase 18 evaluator remains frozen and opaque.

Delivery state never participates (LOCK 8): this module imports nothing from
``smcsignal.delivery`` and no API accepts a delivery record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.analysis.outcome_tracking import (
    OutcomeStatus,
    OutcomeTrackingConfig,
    SignalOutcome,
    aggregate,
)
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
    SeriesProvenance,
)

from .lifecycle import SignalOutcomeLifecycle
from .models import (
    MonthlyReport,
    MonthlySummary,
    SignalObservation,
    StrategyStats,
    strategy_stats,
)
from .observer import AnalyticsObserver

METHODOLOGY_VERSION = "analytics-ledger-v1"
_SNAPSHOT_KIND = "analytics-ledger-snapshot"
_SNAPSHOT_ID_PREFIX = "ledger:"

_TEXT = "must be a nonempty, trimmed string"


def _require_keys(value: object, expected: frozenset[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AnalysisInputError(f"{name} must be a canonical record object")
    keys = set(value)
    if keys != expected:
        raise AnalysisInputError(f"{name} must contain exactly: {', '.join(sorted(expected))}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} {_TEXT}")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise AnalysisInputError(f"{name} must be an integer")
    return value


def _decimal_text(value: object, name: str) -> Decimal:
    if not isinstance(value, str):
        raise AnalysisInputError(f"{name} must be canonical Decimal text")
    try:
        result = Decimal(value)
    except ArithmeticError as exc:
        raise AnalysisInputError(f"{name} is not valid Decimal text") from exc
    if not result.is_finite():
        raise AnalysisInputError(f"{name} must be a finite Decimal")
    return result


def _instant_text(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise AnalysisInputError(f"{name} must be an ISO-8601 instant")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AnalysisInputError(f"{name} is not a valid ISO-8601 instant") from exc
    if result.utcoffset() is None:
        raise AnalysisInputError(f"{name} must be timezone aware")
    return result


def _optional_decimal_text(value: object, name: str) -> Decimal | None:
    return None if value is None else _decimal_text(value, name)


def _optional_integer(value: object, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _optional_instant_text(value: object, name: str) -> datetime | None:
    return None if value is None else _instant_text(value, name)


_SERIES_KEYS = frozenset({"symbol", "timeframe", "venue", "provider", "dataset_id"})
_CANDLE_KEYS = frozenset({"series", "candle_index", "opened_at", "closed_at"})
_EVIDENCE_REFERENCE_KEYS = frozenset({"evidence_id", "series", "available_at"})
_PROVENANCE_KEYS = frozenset(
    {
        "evidence_id",
        "series",
        "producer",
        "producer_version",
        "configuration_hash",
        "input_prefix_hash",
        "available_at",
        "source_candles",
        "dependencies",
        "schema_version",
    }
)
_CONFIG_KEYS = frozenset({"enabled", "horizon_bars"})
_OUTCOME_KEYS = frozenset(
    {
        "settings",
        "status",
        "outcome_id",
        "signal_id",
        "setup_identity",
        "symbol",
        "timeframe",
        "horizon_bars",
        "reference",
        "reference_close",
        "candles_observed",
        "mfe_price",
        "mfe_index",
        "mae_price",
        "mae_index",
        "final_index",
        "final_close",
        "created_available_at",
        "evaluated_available_at",
        "finalized_available_at",
        "provenance",
    }
)
_OBSERVATION_KEYS = frozenset({"outcome"})


def _decode_series(value: object, name: str) -> SeriesProvenance:
    record = _require_keys(value, _SERIES_KEYS, name)
    return SeriesProvenance(
        symbol=_text(record["symbol"], f"{name}.symbol"),
        timeframe=_text(record["timeframe"], f"{name}.timeframe"),
        venue=_text(record["venue"], f"{name}.venue"),
        provider=_text(record["provider"], f"{name}.provider"),
        dataset_id=_text(record["dataset_id"], f"{name}.dataset_id"),
    )


def _decode_candle(value: object, name: str) -> CandleReference:
    record = _require_keys(value, _CANDLE_KEYS, name)
    return CandleReference(
        series=_decode_series(record["series"], f"{name}.series"),
        candle_index=_integer(record["candle_index"], f"{name}.candle_index"),
        opened_at=_instant_text(record["opened_at"], f"{name}.opened_at"),
        closed_at=_instant_text(record["closed_at"], f"{name}.closed_at"),
    )


def _decode_evidence_reference(value: object, name: str) -> EvidenceReference:
    record = _require_keys(value, _EVIDENCE_REFERENCE_KEYS, name)
    return EvidenceReference(
        evidence_id=_text(record["evidence_id"], f"{name}.evidence_id"),
        series=_decode_series(record["series"], f"{name}.series"),
        available_at=_instant_text(record["available_at"], f"{name}.available_at"),
    )


def _decode_provenance(value: object, name: str) -> EvidenceProvenance:
    record = _require_keys(value, _PROVENANCE_KEYS, name)
    source = record["source_candles"]
    dependencies = record["dependencies"]
    if not isinstance(source, list) or not isinstance(dependencies, list):
        raise AnalysisInputError(f"{name} collections must be canonical lists")
    return EvidenceProvenance(
        evidence_id=_text(record["evidence_id"], f"{name}.evidence_id"),
        series=_decode_series(record["series"], f"{name}.series"),
        producer=_text(record["producer"], f"{name}.producer"),
        producer_version=_text(record["producer_version"], f"{name}.producer_version"),
        configuration_hash=_text(record["configuration_hash"], f"{name}.configuration_hash"),
        input_prefix_hash=_text(record["input_prefix_hash"], f"{name}.input_prefix_hash"),
        available_at=_instant_text(record["available_at"], f"{name}.available_at"),
        source_candles=tuple(
            _decode_candle(item, f"{name}.source_candles[{index}]")
            for index, item in enumerate(source)
        ),
        dependencies=tuple(
            _decode_evidence_reference(item, f"{name}.dependencies[{index}]")
            for index, item in enumerate(dependencies)
        ),
        schema_version=_integer(record["schema_version"], f"{name}.schema_version"),
    )


def _decode_config(value: object, name: str) -> OutcomeTrackingConfig:
    record = _require_keys(value, _CONFIG_KEYS, name)
    enabled = record["enabled"]
    if type(enabled) is not bool:
        raise AnalysisInputError(f"{name}.enabled must be a boolean")
    return OutcomeTrackingConfig(
        enabled=enabled,
        horizon_bars=_integer(record["horizon_bars"], f"{name}.horizon_bars"),
    )


def _decode_outcome(value: object, name: str, settings: OutcomeTrackingConfig) -> SignalOutcome:
    record = _require_keys(value, _OUTCOME_KEYS, name)
    decoded_settings = _decode_config(record["settings"], f"{name}.settings")
    if decoded_settings != settings:
        raise AnalysisInputError(f"{name}.settings must equal the ledger configuration")
    status_value = record["status"]
    if not isinstance(status_value, str):
        raise AnalysisInputError(f"{name}.status must be a canonical enum value")
    try:
        status = OutcomeStatus(status_value)
    except ValueError as exc:
        raise AnalysisInputError(f"{name}.status is not a known OutcomeStatus") from exc
    return SignalOutcome(
        settings=decoded_settings,
        status=status,
        outcome_id=_text(record["outcome_id"], f"{name}.outcome_id"),
        signal_id=_text(record["signal_id"], f"{name}.signal_id"),
        setup_identity=_text(record["setup_identity"], f"{name}.setup_identity"),
        symbol=_text(record["symbol"], f"{name}.symbol"),
        timeframe=_text(record["timeframe"], f"{name}.timeframe"),
        horizon_bars=_integer(record["horizon_bars"], f"{name}.horizon_bars"),
        reference=_decode_candle(record["reference"], f"{name}.reference"),
        reference_close=_decimal_text(record["reference_close"], f"{name}.reference_close"),
        candles_observed=_integer(record["candles_observed"], f"{name}.candles_observed"),
        mfe_price=_optional_decimal_text(record["mfe_price"], f"{name}.mfe_price"),
        mfe_index=_optional_integer(record["mfe_index"], f"{name}.mfe_index"),
        mae_price=_optional_decimal_text(record["mae_price"], f"{name}.mae_price"),
        mae_index=_optional_integer(record["mae_index"], f"{name}.mae_index"),
        final_index=_optional_integer(record["final_index"], f"{name}.final_index"),
        final_close=_optional_decimal_text(record["final_close"], f"{name}.final_close"),
        created_available_at=_instant_text(
            record["created_available_at"], f"{name}.created_available_at"
        ),
        evaluated_available_at=_optional_instant_text(
            record["evaluated_available_at"], f"{name}.evaluated_available_at"
        ),
        finalized_available_at=_optional_instant_text(
            record["finalized_available_at"], f"{name}.finalized_available_at"
        ),
        provenance=_decode_provenance(record["provenance"], f"{name}.provenance"),
    )


def _decode_observation(
    value: object, name: str, settings: OutcomeTrackingConfig
) -> SignalObservation:
    record = _require_keys(value, _OBSERVATION_KEYS, name)
    return SignalObservation(
        outcome=_decode_outcome(record["outcome"], f"{name}.outcome", settings)
    )


def _content_dict(
    settings: OutcomeTrackingConfig,
    configuration_hash: str,
    observations: tuple[SignalObservation, ...],
    finalized: tuple[SignalOutcome, ...],
) -> dict[str, object]:
    return {
        "methodology": METHODOLOGY_VERSION,
        "kind": _SNAPSHOT_KIND,
        "settings": settings,
        "configuration_hash": configuration_hash,
        "observations": observations,
        "finalized": finalized,
    }


def _snapshot_identity(content: dict[str, object]) -> str:
    return _SNAPSHOT_ID_PREFIX + digest(content)


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """One immutable, content-addressed snapshot of an analytics ledger.

    The snapshot carries exactly what the live observer's public read views
    expose: the ordered publication observations (each with its initial OPEN
    outcome), the market-evaluated finals in observation order, and the shared
    outcome configuration. ``snapshot_id`` is recomputed from the contents in
    the constructor, so two snapshots are byte-comparable and any externally
    supplied identity is never trusted.
    """

    methodology: str
    settings: OutcomeTrackingConfig
    configuration_hash: str
    observations: tuple[SignalObservation, ...]
    finalized: tuple[SignalOutcome, ...]
    snapshot_id: str

    def __post_init__(self) -> None:
        if self.methodology != METHODOLOGY_VERSION:
            raise AnalysisInputError(f"ledger snapshots require methodology {METHODOLOGY_VERSION}")
        if not isinstance(self.settings, OutcomeTrackingConfig):
            raise AnalysisInputError("snapshot requires OutcomeTrackingConfig")
        _text(self.configuration_hash, "configuration_hash")
        for label, records, kind in (
            ("observations", self.observations, SignalObservation),
            ("finalized", self.finalized, SignalOutcome),
        ):
            if not isinstance(records, tuple) or not all(
                isinstance(record, kind) for record in records
            ):
                raise AnalysisInputError(f"{label} must be a tuple of {kind.__name__}")
        observed_ids = [observation.outcome_id for observation in self.observations]
        if len(set(observed_ids)) != len(observed_ids):
            raise AnalysisInputError("duplicate observations are forbidden")
        finalized_ids = [record.outcome_id for record in self.finalized]
        if len(set(finalized_ids)) != len(finalized_ids):
            raise AnalysisInputError("duplicate finalized outcomes are forbidden")
        observed = {observation.outcome_id: observation for observation in self.observations}
        for record in self.finalized:
            if record.status is OutcomeStatus.OPEN:
                raise AnalysisInputError("a finalized record is not open")
            observation = observed.get(record.outcome_id)
            if observation is None:
                raise AnalysisInputError("only observed outcomes can appear as finalized")
            if record.settings != self.settings:
                raise AnalysisInputError("finalized records must use the ledger configuration")
            if record.signal_id != observation.signal_id:
                raise AnalysisInputError("finalized records must track the observed signal")
        expected = _snapshot_identity(
            _content_dict(self.settings, self.configuration_hash, self.observations, self.finalized)
        )
        if self.snapshot_id != expected:
            raise AnalysisInputError("snapshot_id must be the content-addressed identity")


def snapshot_ledger(source: AnalyticsObserver | SignalOutcomeLifecycle) -> LedgerSnapshot:
    """Pure projection of a live ledger's public read views; never mutates it.

    Accepts the Phase 26A observer directly or a Phase 26B lifecycle (whose
    observer is the single authoritative ledger). No other source is accepted:
    delivery records, engine frames, and evaluator internals cannot enter.
    """

    observer = source.observer if isinstance(source, SignalOutcomeLifecycle) else source
    if not isinstance(observer, AnalyticsObserver):
        raise AnalysisInputError(
            "snapshot_ledger requires an AnalyticsObserver or a SignalOutcomeLifecycle"
        )
    observations = observer.observations
    finalized = observer.finalized_outcomes
    content = _content_dict(observer.config, observer.configuration_hash, observations, finalized)
    return LedgerSnapshot(
        methodology=METHODOLOGY_VERSION,
        settings=observer.config,
        configuration_hash=observer.configuration_hash,
        observations=observations,
        finalized=finalized,
        snapshot_id=_snapshot_identity(content),
    )


def ledger_bytes(snapshot: LedgerSnapshot) -> bytes:
    """Canonical bytes of one snapshot, through the existing evidence canon.

    The document embeds the content-addressed ``snapshot_id``; ``load_ledger_bytes``
    recomputes it rather than trusting the embedded value.
    """

    if not isinstance(snapshot, LedgerSnapshot):
        raise AnalysisInputError("ledger_bytes requires a LedgerSnapshot")
    document = dict(
        _content_dict(
            snapshot.settings,
            snapshot.configuration_hash,
            snapshot.observations,
            snapshot.finalized,
        )
    )
    document["snapshot_id"] = snapshot.snapshot_id
    return canonical_bytes(document)


_DOCUMENT_KEYS = frozenset(
    {
        "methodology",
        "kind",
        "settings",
        "configuration_hash",
        "observations",
        "finalized",
        "snapshot_id",
    }
)


def load_ledger_bytes(data: bytes) -> LedgerSnapshot:
    """Restore a snapshot from canonical bytes; never trusts the embedded id.

    Validation order: parse, exact document keys, methodology/kind, recomputed
    content digest versus the embedded identity, then reconstruction through
    the frozen model constructors (which run the frozen Phase 18/26A
    invariants). Any tampering or malformed content raises.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise AnalysisInputError("load_ledger_bytes requires canonical bytes")
    try:
        document = json.loads(bytes(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError("ledger bytes are not canonical JSON") from exc
    record = _require_keys(document, _DOCUMENT_KEYS, "ledger document")
    if record["methodology"] != METHODOLOGY_VERSION:
        raise AnalysisInputError(f"ledger document requires methodology {METHODOLOGY_VERSION}")
    if record["kind"] != _SNAPSHOT_KIND:
        raise AnalysisInputError(f"ledger document kind must be {_SNAPSHOT_KIND}")
    embedded_id = record["snapshot_id"]
    if not isinstance(embedded_id, str):
        raise AnalysisInputError("snapshot_id must be a string")
    settings = _decode_config(record["settings"], "ledger document.settings")
    observations_value = record["observations"]
    finalized_value = record["finalized"]
    if not isinstance(observations_value, list) or not isinstance(finalized_value, list):
        raise AnalysisInputError("ledger collections must be canonical lists")
    observations = tuple(
        _decode_observation(item, f"observations[{index}]", settings)
        for index, item in enumerate(observations_value)
    )
    finalized = tuple(
        _decode_outcome(item, f"finalized[{index}]", settings)
        for index, item in enumerate(finalized_value)
    )
    recomputed = _snapshot_identity(
        _content_dict(
            settings,
            _text(record["configuration_hash"], "configuration_hash"),
            observations,
            finalized,
        )
    )
    if embedded_id != recomputed:
        raise AnalysisInputError(
            "ledger document failed the recomputed content-addressed identity check"
        )
    return LedgerSnapshot(
        methodology=METHODOLOGY_VERSION,
        settings=settings,
        configuration_hash=_text(record["configuration_hash"], "configuration_hash"),
        observations=observations,
        finalized=finalized,
        snapshot_id=recomputed,
    )


class RestoredAnalyticsLedger:
    """Read-only historical ledger restored from a snapshot. Facts only.

    Exposes exactly the live observer's read views — observations, open
    outcomes, finalized outcomes, ``StrategyStats``, and the UTC
    ``MonthlyReport`` — computed through the same frozen Phase 18 aggregate
    machinery. It accepts no new observations, no new finals, and no frames,
    and it resumes no evaluator: continuation after a restart is a future
    phase, and the Phase 18 evaluator remains frozen and opaque.
    """

    def __init__(self, snapshot: LedgerSnapshot) -> None:
        if not isinstance(snapshot, LedgerSnapshot):
            raise AnalysisInputError("restored ledger requires a LedgerSnapshot")
        self._snapshot = snapshot

    @property
    def snapshot(self) -> LedgerSnapshot:
        return self._snapshot

    @property
    def settings(self) -> OutcomeTrackingConfig:
        return self._snapshot.settings

    @property
    def configuration_hash(self) -> str:
        return self._snapshot.configuration_hash

    @property
    def snapshot_id(self) -> str:
        return self._snapshot.snapshot_id

    @property
    def observations(self) -> tuple[SignalObservation, ...]:
        return self._snapshot.observations

    @property
    def open_outcomes(self) -> tuple[SignalOutcome, ...]:
        """Observed outcomes with no market-evaluated final."""

        finalized_ids = {record.outcome_id for record in self._snapshot.finalized}
        return tuple(
            observation.outcome
            for observation in self._snapshot.observations
            if observation.outcome_id not in finalized_ids
        )

    @property
    def finalized_outcomes(self) -> tuple[SignalOutcome, ...]:
        return self._snapshot.finalized

    @property
    def strategy_stats(self) -> StrategyStats:
        return strategy_stats(
            self._snapshot.finalized,
            open_count=len(self._snapshot.observations) - len(self._snapshot.finalized),
        )

    def monthly_report(self) -> MonthlyReport:
        """Chronological UTC-month report; identical grouping to the live observer."""

        finalized_map = {record.outcome_id: record for record in self._snapshot.finalized}
        by_month: dict[str, list[SignalOutcome]] = {}
        for observation in self._snapshot.observations:
            current = finalized_map.get(observation.outcome_id, observation.outcome)
            by_month.setdefault(current.reference.opened_at.strftime("%Y-%m"), []).append(current)
        months: list[MonthlySummary] = []
        for key in sorted(by_month):
            records = tuple(by_month[key])
            finalized = tuple(
                record for record in records if record.status is not OutcomeStatus.OPEN
            )
            months.append(
                MonthlySummary(
                    month=key,
                    summary=aggregate(
                        finalized,
                        total_buy_signals=len(records),
                        open_count=len(records) - len(finalized),
                    ),
                )
            )
        return MonthlyReport(months=tuple(months), totals=self.strategy_stats)
