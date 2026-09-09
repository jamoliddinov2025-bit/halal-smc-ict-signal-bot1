"""Phase 24B configuration tests: strict, offline, no secrets."""

from __future__ import annotations

import pytest

from smcsignal.analysis.errors import AnalysisConfigurationError
from smcsignal.delivery import DeliveryConfig, load_delivery_config

_FULL = {
    "enabled": True,
    "html_escape": True,
    "max_caption_length": 4000,
    "split_long_messages": True,
    "show_signal_id": True,
    "show_setup_context": True,
    "show_halal_status": True,
    "show_timestamps": True,
    "show_chart_unavailable_marker": True,
}


def test_default_config_is_safe_and_offline() -> None:
    config = DeliveryConfig()
    assert config.html_escape is True
    assert config.max_caption_length > 0
    # no secret-bearing fields exist on the config schema
    from dataclasses import fields

    field_names = {field.name for field in fields(DeliveryConfig)}
    for banned in ("token", "secret", "chat_id", "api_key", "bot"):
        assert not any(banned in name for name in field_names)


def test_config_never_allows_html_escaping_off() -> None:
    with pytest.raises(AnalysisConfigurationError):
        DeliveryConfig(html_escape=False)


def test_config_rejects_zero_caption_length() -> None:
    with pytest.raises(AnalysisConfigurationError):
        DeliveryConfig(max_caption_length=0)


def test_load_delivery_config_round_trip(tmp_path) -> None:
    path = tmp_path / "delivery.toml"
    lines = ["[delivery]"]
    for key, value in _FULL.items():
        lines.append(f"{key} = {str(value).lower()}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    loaded = load_delivery_config(path)
    assert loaded.enabled is True
    assert loaded.max_caption_length == 4000


def test_load_delivery_config_rejects_unknown_key(tmp_path) -> None:
    path = tmp_path / "delivery.toml"
    path.write_text('[delivery]\nenabled = true\ntelegram_bot_token = "x"\n', encoding="utf-8")
    with pytest.raises(AnalysisConfigurationError):
        load_delivery_config(path)


def test_load_delivery_config_missing_file_raises(tmp_path) -> None:
    with pytest.raises(AnalysisConfigurationError):
        load_delivery_config(tmp_path / "nope.toml")
