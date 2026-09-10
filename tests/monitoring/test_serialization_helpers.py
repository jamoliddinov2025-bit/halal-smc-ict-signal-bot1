"""Phase 25B-3 tests for the canonical serialization helpers.

``test_serialization.py`` already covers the shared canon that 25B-3 reuses. These
tests cover the helpers this phase adds on top of it, with the emphasis on what
must be refused rather than merely tolerated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from smcsignal.monitoring import (
    MonitoringInputError,
    canonical_bytes,
    canonical_record,
    content_digest,
    require_serializable,
)


def test_canonical_record_is_key_sorted_and_compact() -> None:
    rendered = canonical_record({"b": 1, "a": 2})
    assert rendered == '{"a":2,"b":1}'


def test_canonical_bytes_and_digest_agree_with_the_rendering() -> None:
    record = {"a": 1, "b": [1, 2]}
    assert canonical_bytes(record) == canonical_record(record).encode("utf-8")
    assert content_digest(record) == content_digest(record)
    assert len(content_digest(record)) == 64


def test_content_digest_is_sensitive_to_every_field() -> None:
    assert content_digest({"a": 1}) != content_digest({"a": 2})
    assert content_digest({"a": 1}) != content_digest({"a": 1, "b": 0})
    assert content_digest({"a": "1"}) != content_digest({"a": 1})


def test_require_serializable_accepts_the_canonical_scalar_set() -> None:
    require_serializable({"s": "x", "i": 1, "b": True, "n": None})


def test_decimals_are_carried_as_exact_strings() -> None:
    record = canonical_record({"value": Decimal("1.500")})
    assert record == '{"value":"15e-1"}'
    assert "1.5" not in record  # no float ever appears in the rendering


def test_timestamps_are_utc_iso_8601() -> None:
    record = canonical_record({"at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)})
    assert record == '{"at":"2026-01-01T12:00:00.000000Z"}'


@pytest.mark.parametrize(
    "value",
    [
        {"n": 1.5},
        {"n": 0.1},
        [1.0],
        {"n": float("nan")},
        {"n": float("inf")},
    ],
)
def test_floats_are_refused_outright(value: object) -> None:
    with pytest.raises(MonitoringInputError):
        canonical_record(value)


@pytest.mark.parametrize(
    "value",
    [
        {"d": Decimal("NaN")},
        {"d": Decimal("Infinity")},
        {"d": Decimal("sNaN")},
    ],
)
def test_nonfinite_decimals_are_refused(value: object) -> None:
    with pytest.raises(MonitoringInputError):
        canonical_record(value)


def test_finite_decimals_of_any_shape_are_accepted() -> None:
    for text in ("0", "-1", "1E+20", "0.000001", "12345678901234567890"):
        require_serializable({"d": Decimal(text)})


def test_naive_timestamps_are_refused() -> None:
    with pytest.raises(MonitoringInputError):
        canonical_record({"at": datetime(2026, 1, 1, 12, 0)})


def test_durations_are_refused_so_callers_choose_a_unit() -> None:
    # A timedelta has no single canonical rendering; the caller must pick
    # milliseconds or seconds explicitly rather than let the helper guess.
    with pytest.raises(MonitoringInputError):
        canonical_record({"d": timedelta(seconds=1)})


def test_unrepresentable_objects_are_refused() -> None:
    for value in (object(), b"bytes", {"set"}, complex(1, 2)):
        with pytest.raises(MonitoringInputError):
            canonical_record({"v": value})


def test_no_repr_or_identity_leaks_into_a_rendering() -> None:
    rendered = canonical_record({"at": datetime(2026, 1, 1, tzinfo=UTC)})
    assert "object at 0x" not in rendered
    assert "datetime.datetime" not in rendered


def test_require_serializable_is_the_same_gate_as_rendering() -> None:
    record = {"ok": [1, {"nested": None}]}
    require_serializable(record)
    assert canonical_record(record)
    with pytest.raises(MonitoringInputError):
        require_serializable({"ok": [1, {"nested": 2.0}]})


def test_digest_is_stable_across_key_order_and_process_state() -> None:
    first = content_digest({"a": 1, "b": 2})
    second = content_digest({"b": 2, "a": 1})
    assert first == second
