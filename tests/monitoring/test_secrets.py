"""Phase 25B-1 secret-safety tests: no token, chat id, or content can be stored."""

from __future__ import annotations

import re
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import pytest

import smcsignal
from smcsignal.monitoring import (
    HealthCode,
    MonitoredComponent,
    MonitoringConfig,
    MonitoringInputError,
    build_health_event,
)

MONITORING = Path(smcsignal.__file__).resolve().parent / "monitoring"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_SECRET_SHAPED = (
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),  # messaging bot token shape
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # cloud access key id shape
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # api key shape
    re.compile(r"\b\d{9,13}\b"),  # bare numeric account/chat id shape
)


def test_no_monitoring_source_contains_a_secret_shaped_literal() -> None:
    offenders: list[str] = []
    for path in sorted(MONITORING.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in _SECRET_SHAPED):
            offenders.append(path.name)
    assert offenders == []


def test_config_schema_cannot_carry_a_credential() -> None:
    names = {field.name for field in fields(MonitoringConfig)}
    assert not any("token" in name or "secret" in name or "chat" in name for name in names)


@pytest.mark.parametrize(
    "subject",
    [
        "1234567890",  # bare chat id
        "-1001234567890",  # bare channel id
        "123456789:AAHsomeRealLookingTokenValue",
        "AAHsomeRealLookingTokenValue",
    ],
)
def test_event_refuses_a_raw_credential_or_destination(subject: str) -> None:
    with pytest.raises(MonitoringInputError):
        build_health_event(
            component=MonitoredComponent.DELIVERY,
            code=HealthCode.DELIVERY_FAILED,
            observed_at=NOW,
            subject_id=subject,
        )


def test_a_redacted_destination_is_accepted() -> None:
    event = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_FAILED,
        observed_at=NOW,
        subject_id="<redacted>:7890",
    )
    subject = event.subject_id
    assert subject is not None and "<redacted>" in subject


def test_serialized_event_carries_no_secret_material() -> None:
    from smcsignal.analysis.liquidity.evidence import evidence_json

    event = build_health_event(
        component=MonitoredComponent.DELIVERY,
        code=HealthCode.DELIVERY_CONFIG_ERROR,
        observed_at=NOW,
        subject_id="<redacted>:7890",
        detail=(("category", "config"),),
    )
    rendered = evidence_json(event.to_record())
    assert "<redacted>" in rendered
    assert not any(pattern.search(rendered) for pattern in _SECRET_SHAPED)
