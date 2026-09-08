"""Signal identities using the existing canonical/provenance contract."""

from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest, provenance
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference
from smcsignal.analysis.setup_quality.calculation import (
    nested_displacement,
    nested_fvg,
    nested_mtf,
    nested_order_block,
    nested_ote,
)
from smcsignal.analysis.signal_eligibility.models import EligibilitySnapshot
from smcsignal.analysis.signal_engine.config import METHODOLOGY_VERSION, SignalEngineConfig
from smcsignal.analysis.signal_engine.models import (
    Signal,
    SignalCandidate,
    SignalReason,
    current_observation,
)


def configuration_artifact(config: SignalEngineConfig) -> bytes:
    return canonical_bytes(
        {
            "methodology": METHODOLOGY_VERSION,
            "settings": config,
            "spot_only": True,
            "duplicate": "one_per_setup",
            "execution": False,
            "leverage": False,
            "futures": False,
            "short_trade": False,
            "entry": False,
            "optimization": False,
        }
    )


def setup_identity(frame: EligibilitySnapshot) -> str:
    """Hash only existing nested evidence IDs. Missing features are omitted, never invented.

    Per-candle snapshot IDs are excluded so one_per_setup can collapse repeats.
    MSS/Breaker/Mitigation IDs are omitted unless a nested join actually exists.
    """

    series = frame.provenance.series
    halal = frame.upstream.upstream
    mtf = nested_mtf(halal)
    ote = nested_ote(halal)
    payload: dict[str, object] = {
        "symbol": series.symbol,
        "timeframe": series.timeframe,
        "direction": frame.bias,
    }
    mtf_ids = tuple(item.source.evidence_id for item in mtf.evidence)
    if mtf_ids:
        payload["mtf"] = mtf_ids
    if ote.zone is not None:
        payload["ote"] = ote.zone.zone_id
    blocks = tuple(block.event_id for block in nested_order_block(halal).events)
    if blocks:
        payload["order_block"] = blocks
    gaps = tuple(gap.event_id for gap in nested_fvg(halal).events)
    if gaps:
        payload["fvg"] = gaps
    moves = tuple(event.event_id for event in nested_displacement(halal).events)
    if moves:
        payload["displacement"] = moves
    return f"spot-setup:{digest(payload)}"


def collect_evidence(frame: EligibilitySnapshot) -> tuple[EvidenceReference, ...]:
    observation = current_observation(frame)
    cutoff = observation.available_at
    halal = frame.upstream.upstream
    refs = [
        frame.provenance.as_reference(),
        frame.decision.provenance.as_reference(),
        *frame.decision.evidence,
        nested_fvg(halal).provenance.as_reference(),
        nested_displacement(halal).provenance.as_reference(),
        *(gap.provenance.as_reference() for gap in nested_fvg(halal).events),
        *(event.provenance.as_reference() for event in nested_displacement(halal).events),
        *(sweep.provenance.as_reference() for sweep in nested_displacement(halal).liquidity.sweeps),
    ]
    return tuple(dict.fromkeys(item for item in refs if item.available_at <= cutoff))


def candidate_provenance(
    upstream: EligibilitySnapshot,
    signal: Signal,
    reasons: tuple[SignalReason, ...],
    evidence: tuple[EvidenceReference, ...],
    config_hash: str,
) -> EvidenceProvenance:
    observation = current_observation(upstream)
    return provenance(
        series=upstream.provenance.series,
        producer="signal-candidate",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=observation.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "status": signal.status,
            "direction": signal.direction,
            "setup_identity": signal.setup_identity,
            "reasons": reasons,
            "evidence": tuple(item.evidence_id for item in evidence),
        },
        source_candles=(observation.reference,),
        dependencies=(upstream.provenance.as_reference(),),
    )


def snapshot_provenance(
    upstream: EligibilitySnapshot, candidate: SignalCandidate, config_hash: str
) -> EvidenceProvenance:
    observation = current_observation(upstream)
    return provenance(
        series=upstream.provenance.series,
        producer="signal-frame",
        configuration_hash=config_hash,
        input_prefix_hash=upstream.provenance.input_prefix_hash,
        available_at=upstream.provenance.available_at,
        key={
            "upstream": upstream.provenance.evidence_id,
            "candidate": candidate.provenance.evidence_id,
        },
        source_candles=(observation.reference,),
        dependencies=(
            upstream.provenance.as_reference(),
            candidate.provenance.as_reference(),
        ),
    )
