"""Deterministic identities and canonical serialization for Phase 23 records.

Identities are digests over canonical, frozen inputs only: no wall-clock
timestamps, randomness, environment-dependent values, or unordered
serialization. Repeated identical inputs always produce identical identities.
The canonical-JSON machinery is the existing liquidity evidence helper reused
unchanged so Decimals, dataclasses, and enums serialize losslessly.
"""

from __future__ import annotations

import re
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.improvement.config import METHODOLOGY_VERSION
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest

_BASELINE_HASH = re.compile(r"^[0-9a-f]{64}$")


def check_baseline_hash(value: object) -> str:
    """A baseline reference must be a 64-char lowercase SHA-256 hex digest."""
    if not isinstance(value, str) or not _BASELINE_HASH.fullmatch(value):
        raise AnalysisInputError(
            "baseline_hash must be a 64-character lowercase hex SHA-256 digest"
        )
    return value


def compose_id(prefix: str, payload: object) -> str:
    """Deterministic ``prefix:<digest>`` from a canonical frozen payload."""
    return f"{prefix}:{digest(payload)}"


def hypothesis_identity(
    *,
    title: str,
    affected_component: str,
    observed_weakness: str,
    proposed_change: str,
    baseline_hash: str | None,
    evidence_references: tuple[str, ...] = (),
) -> str:
    """Deterministic identity of an improvement hypothesis (a proposal only)."""
    return compose_id(
        "hypothesis",
        {
            "methodology": METHODOLOGY_VERSION,
            "kind": "improvement-hypothesis",
            "title": title,
            "affected_component": affected_component,
            "observed_weakness": observed_weakness,
            "proposed_change": proposed_change,
            "baseline_hash": baseline_hash,
            "evidence_references": sorted(evidence_references),
        },
    )


def decision_identity(
    *,
    candidate_id: str,
    decision: str,
    operator_identity: str,
    rationale: str,
    evidence_id: str | None,
) -> str:
    """Deterministic identity of one explicit human decision record."""
    return compose_id(
        "decision",
        {
            "methodology": METHODOLOGY_VERSION,
            "kind": "human-decision",
            "candidate_id": candidate_id,
            "decision": decision,
            "operator_identity": operator_identity,
            "rationale": rationale,
            "evidence_id": evidence_id,
        },
    )


def sha256_hex(value: object) -> str:
    """Expose the canonical digest helper for callers that need raw digests."""
    return sha256(canonical_bytes(value)).hexdigest()
