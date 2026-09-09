"""Deterministic outcome identities and provenance over existing signal frames."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest, provenance
from smcsignal.analysis.outcome_tracking.config import (
    METHODOLOGY_VERSION,
    OutcomeTrackingConfig,
)
from smcsignal.analysis.outcome_tracking.models import (
    AnalyticsSummary,
    SignalOutcome,
    current_observation,
)
from smcsignal.analysis.provenance import (
    CandleReference,
    EvidenceProvenance,
    EvidenceReference,
)
from smcsignal.analysis.signal_engine.models import SignalSnapshot


def configuration_artifact(config: OutcomeTrackingConfig) -> bytes:
    """Frozen methodology artifact; every non-goal is declared false."""

    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "scope": "buy_signal_only",
            "reference_price": "signal_candle_close",
            "classification": "exact_sign_no_threshold",
            "end_of_series_flush": "none",
            "execution": False,
            "entry": False,
            "exit": False,
            "stop": False,
            "target": False,
            "fees": False,
            "slippage": False,
            "position_sizing": False,
            "sell": False,
            "short_trade": False,
            "leverage": False,
            "optimization": False,
        }
    )


def outcome_identity(frame: SignalSnapshot, settings: OutcomeTrackingConfig) -> str:
    """Stable outcome identity from signal facts and configuration only.

    No future candle, evaluation version, or snapshot ID participates, so every
    lifecycle version of one outcome shares this identity.
    """

    payload = {
        "methodology": METHODOLOGY_VERSION,
        "signal_id": frame.signal_id,
        "horizon_bars": settings.horizon_bars,
    }
    return f"spot-outcome:{digest(payload)}"


def record_provenance(
    frame: SignalSnapshot,
    settings: OutcomeTrackingConfig,
    outcome_id: str,
    key: object,
    *,
    available_at: datetime,
    source_candles: tuple[CandleReference, ...],
    dependencies: tuple[EvidenceReference, ...],
    config_hash: str,
) -> EvidenceProvenance:
    """One immutable outcome-version evidence record for the current cutoff."""

    return provenance(
        series=frame.provenance.series,
        producer="outcome-record",
        configuration_hash=config_hash,
        input_prefix_hash=frame.provenance.input_prefix_hash,
        available_at=available_at,
        key={"outcome_id": outcome_id, "settings": settings, "facts": key},
        source_candles=source_candles,
        dependencies=dependencies,
    )


def snapshot_provenance(
    frame: SignalSnapshot,
    records: Sequence[SignalOutcome],
    analytics: AnalyticsSummary,
    config_hash: str,
) -> EvidenceProvenance:
    """Per-frame outcome evidence: upstream signal, touched versions, analytics."""

    observation = current_observation(frame)
    return provenance(
        series=frame.provenance.series,
        producer="outcome-frame",
        configuration_hash=config_hash,
        input_prefix_hash=frame.provenance.input_prefix_hash,
        available_at=observation.available_at,
        key={
            "upstream": frame.provenance.evidence_id,
            "records": tuple(record.provenance.evidence_id for record in records),
            "analytics": analytics,
        },
        source_candles=(observation.reference,),
        dependencies=(
            frame.provenance.as_reference(),
            *(record.provenance.as_reference() for record in records),
        ),
    )
