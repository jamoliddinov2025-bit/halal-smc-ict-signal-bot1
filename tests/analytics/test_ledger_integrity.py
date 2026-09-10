"""Phase 26C integrity: tampered or malformed ledger bytes are always rejected.

The embedded ``snapshot_id`` is never trusted: load recomputes the
content-addressed digest and refuses mismatches. For deeper layers, the tests
re-issue a valid digest over the mutated content (simulating an attacker that
controls the serializer) and prove the frozen model constructors and strict
schema checks still reject the forgery — validation reuse, not re-implementation.
"""

from __future__ import annotations

import json

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.analytics import ledger_bytes, load_ledger_bytes, snapshot_ledger
from tests.analytics.test_ledger import lifecycle_for


def _blob(label="mixed"):
    return ledger_bytes(snapshot_ledger(lifecycle_for(label)))


def _reissued(doc: dict) -> bytes:
    """Re-sign a mutated document so only the deep validation layers can catch it."""

    content = {key: value for key, value in doc.items() if key != "snapshot_id"}
    doc["snapshot_id"] = "ledger:" + digest(content)
    return canonical_bytes(doc)


def _mutated(blob: bytes, mutate) -> bytes:
    doc = json.loads(blob)
    mutate(doc)
    return _reissued(doc)


def test_single_byte_tamper_is_rejected() -> None:
    blob = _blob()
    flipped = bytearray(blob)
    flipped[len(flipped) // 2] ^= 0x01
    with pytest.raises(AnalysisInputError):
        load_ledger_bytes(bytes(flipped))


def test_truncated_bytes_are_rejected() -> None:
    blob = _blob()
    with pytest.raises(AnalysisInputError):
        load_ledger_bytes(blob[: len(blob) // 2])
    with pytest.raises(AnalysisInputError):
        load_ledger_bytes(blob[:-1])


def test_garbage_and_non_bytes_inputs_are_rejected() -> None:
    for bad in (b"", b"not json", b"\xff\xfe\x00", b"null", b"[]"):
        with pytest.raises(AnalysisInputError):
            load_ledger_bytes(bad)
    with pytest.raises(AnalysisInputError):
        load_ledger_bytes('{"snapshot_id": "x"}')  # type: ignore[arg-type]


def test_an_altered_embedded_identity_is_never_trusted() -> None:
    doc = json.loads(_blob())
    doc["snapshot_id"] = "ledger:" + "0" * 64
    with pytest.raises(AnalysisInputError, match="identity"):
        load_ledger_bytes(canonical_bytes(doc))


def test_document_key_set_is_exact() -> None:
    with pytest.raises(AnalysisInputError, match="exactly"):
        load_ledger_bytes(_mutated(_blob(), lambda doc: doc.update({"extra": 1})))
    with pytest.raises(AnalysisInputError, match="exactly"):
        load_ledger_bytes(_mutated(_blob("win"), lambda doc: doc.pop("finalized")))


def test_methodology_and_kind_are_validated() -> None:
    with pytest.raises(AnalysisInputError, match="methodology"):
        load_ledger_bytes(
            _mutated(_blob("win"), lambda doc: doc.update({"methodology": "ledger-v9"}))
        )
    with pytest.raises(AnalysisInputError, match="kind"):
        load_ledger_bytes(
            _mutated(_blob("win"), lambda doc: doc.update({"kind": "something-else"}))
        )


def test_unknown_status_is_rejected() -> None:
    def corrupt(doc):
        doc["finalized"][0]["status"] = "SURE_WIN"

    with pytest.raises(AnalysisInputError, match="OutcomeStatus"):
        load_ledger_bytes(_mutated(_blob("win"), corrupt))


def test_forged_status_is_rejected_by_the_frozen_phase18_model() -> None:
    """WIN flipped to LOSS with unchanged finals violates the exact-sign rule.

    The digest is re-issued over the forgery, so only the frozen SignalOutcome
    constructor can catch it — proof that load reuses Phase 18 validation.
    """

    def corrupt(doc):
        doc["finalized"][0]["status"] = "LOSS"

    with pytest.raises(AnalysisInputError, match="exact sign"):
        load_ledger_bytes(_mutated(_blob("win"), corrupt))


def test_outcome_record_key_set_is_exact() -> None:
    def add_key(doc):
        doc["finalized"][0]["confidence"] = "high"

    with pytest.raises(AnalysisInputError, match="exactly"):
        load_ledger_bytes(_mutated(_blob("win"), add_key))


def test_invalid_datetime_representations_are_rejected() -> None:
    def not_a_date(doc):
        doc["finalized"][0]["created_available_at"] = "yesterday"

    with pytest.raises(AnalysisInputError, match="ISO-8601"):
        load_ledger_bytes(_mutated(_blob("win"), not_a_date))

    def naive_date(doc):
        doc["finalized"][0]["created_available_at"] = "2024-01-01T08:00:00"

    with pytest.raises(AnalysisInputError, match="timezone aware"):
        load_ledger_bytes(_mutated(_blob("win"), naive_date))


def test_float_injection_is_rejected_where_the_canon_forbids_it() -> None:
    doc = json.loads(_blob("win"))
    doc["finalized"][0]["reference_close"] = 24.5  # a float, never canonical
    with pytest.raises(AnalysisInputError, match="Decimal"):
        # stdlib json writes the float; the evidence canon cannot re-sign it,
        # and the ledger decoder refuses it before any identity question
        load_ledger_bytes(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def test_ledger_module_has_no_delivery_monitoring_data_or_io_reference() -> None:
    import ast
    from pathlib import Path

    module = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analytics" / "ledger.py"
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
            assert all(root not in ("socket", "urllib", "http", "sqlite3") for root in roots)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            assert not node.module.startswith(
                ("smcsignal.delivery", "smcsignal.monitoring", "smcsignal.data")
            ), f"ledger imports {node.module}"
        if isinstance(node, (ast.Name, ast.Attribute)):
            identifier = node.id if isinstance(node, ast.Name) else node.attr
            assert "Delivery" not in identifier and "Telegram" not in identifier
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("open", "print", "input", "exec", "eval")
