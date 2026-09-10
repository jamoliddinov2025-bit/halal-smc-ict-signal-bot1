"""Phase 25B-1 configuration tests: strict, offline, observation-only keys."""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import pytest

from smcsignal.monitoring import (
    MonitoringConfig,
    MonitoringConfigurationError,
    load_monitoring_config,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "monitoring.example.toml"

_FULL = {
    "enabled": False,
    "stale_after_intervals": 3,
    "gap_tolerance_intervals": 0,
    "repeated_failure_threshold": 3,
    "distribution_shift_low": "0",
    "distribution_shift_high": "1",
    "max_events_per_run": 10000,
}


def _render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _write(tmp_path: Path, table: dict[str, object]) -> Path:
    path = tmp_path / "monitoring.toml"
    lines = ["[monitoring]"]
    for key, value in table.items():
        lines.append(f"{key} = {_render(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_default_config_is_inert() -> None:
    config = MonitoringConfig()
    assert config.enabled is False
    assert config.distribution_shift_low == Decimal(0)
    assert config.distribution_shift_high == Decimal(1)
    assert config.max_events_per_run > 0


def test_config_schema_has_no_secret_or_alerting_field() -> None:
    names = {field.name for field in fields(MonitoringConfig)}
    for banned in ("token", "secret", "chat_id", "api_key", "bot", "url", "webhook"):
        assert not any(banned in name for name in names), banned
    # Alerting is deliberately deferred to a later, separately approved phase.
    assert not any("alert" in name for name in names)


def test_config_has_no_signal_affecting_field() -> None:
    names = {field.name for field in fields(MonitoringConfig)}
    for banned in ("threshold",):
        # The only thresholds present describe observation, not signal logic.
        assert all("signal" not in name for name in names), banned


@pytest.mark.parametrize(
    "kwargs",
    [
        {"enabled": "yes"},
        {"stale_after_intervals": 0},
        {"stale_after_intervals": 1.5},
        {"gap_tolerance_intervals": -1},
        {"repeated_failure_threshold": 0},
        {"max_events_per_run": 0},
        {"distribution_shift_low": 0},
        {"distribution_shift_high": Decimal("nan")},
        {"distribution_shift_low": Decimal("-0.1")},
        {"distribution_shift_high": Decimal("1.5")},
        {"distribution_shift_low": Decimal("0.5"), "distribution_shift_high": Decimal("0.5")},
    ],
)
def test_config_rejects_unsafe_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(MonitoringConfigurationError):
        MonitoringConfig(**kwargs)  # type: ignore[arg-type]


def test_load_round_trip(tmp_path: Path) -> None:
    loaded = load_monitoring_config(_write(tmp_path, {**_FULL, "stale_after_intervals": 5}))
    assert loaded.enabled is False
    assert loaded.stale_after_intervals == 5
    assert loaded.distribution_shift_low == Decimal(0)


def test_load_rejects_unknown_key(tmp_path: Path) -> None:
    with pytest.raises(MonitoringConfigurationError):
        load_monitoring_config(_write(tmp_path, {**_FULL, "telegram_bot_token": "x"}))


def test_load_rejects_missing_key(tmp_path: Path) -> None:
    incomplete = {key: value for key, value in _FULL.items() if key != "max_events_per_run"}
    with pytest.raises(MonitoringConfigurationError):
        load_monitoring_config(_write(tmp_path, incomplete))


def test_load_rejects_an_unquoted_float_threshold(tmp_path: Path) -> None:
    # Thresholds are exact decimals. A TOML float would introduce binary
    # rounding into monitoring arithmetic, so it is refused at the boundary.
    with pytest.raises(MonitoringConfigurationError):
        load_monitoring_config(_write(tmp_path, {**_FULL, "distribution_shift_low": 0.0}))


def test_load_rejects_an_invalid_decimal_string(tmp_path: Path) -> None:
    with pytest.raises(MonitoringConfigurationError):
        load_monitoring_config(_write(tmp_path, {**_FULL, "distribution_shift_low": "abc"}))


def test_load_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MonitoringConfigurationError):
        load_monitoring_config(tmp_path / "absent.toml")


def test_shipped_example_matches_the_documented_defaults() -> None:
    assert EXAMPLE.is_file()
    assert load_monitoring_config(EXAMPLE) == MonitoringConfig()
