"""Deterministic indicator-frame identities over existing displacement evidence."""

from __future__ import annotations

from decimal import Decimal

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.indicators.config import (
    METHODOLOGY_VERSION,
    IndicatorsConfig,
)
from smcsignal.analysis.indicators.models import current_observation
from smcsignal.analysis.liquidity.evidence import canonical_bytes, provenance
from smcsignal.analysis.provenance import EvidenceProvenance


def configuration_artifact(config: IndicatorsConfig) -> bytes:
    """Frozen methodology artifact; the supporting-only role is declared false-by-fact."""

    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "role": "context_and_visualization_only",
            "signal_generation": False,
            "gate": False,
            "veto": False,
            "threshold": False,
            "atr_source": "phase5_reused",
            "execution": False,
        }
    )


def snapshot_provenance(
    frame: DisplacementSnapshot,
    config: IndicatorsConfig,
    *,
    ema_values: tuple[Decimal | None, ...],
    rsi: Decimal | None,
    atr: Decimal | None,
    volume: Decimal,
    volume_average: Decimal | None,
    volume_ratio: Decimal | None,
    config_hash: str,
) -> EvidenceProvenance:
    """One immutable indicator-frame evidence record for the current cutoff."""

    observation = current_observation(frame)
    return provenance(
        series=frame.provenance.series,
        producer="indicator-frame",
        configuration_hash=config_hash,
        input_prefix_hash=frame.provenance.input_prefix_hash,
        available_at=observation.available_at,
        key={
            "upstream": frame.provenance.evidence_id,
            "settings": config,
            "ema_values": ema_values,
            "rsi": rsi,
            "atr": atr,
            "volume": volume,
            "volume_average": volume_average,
            "volume_ratio": volume_ratio,
        },
        source_candles=(observation.reference,),
        dependencies=(frame.provenance.as_reference(),),
    )
