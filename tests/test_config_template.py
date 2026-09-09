"""Check project metadata and the offline-first market data example."""

import tomllib
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_template() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "config" / "example.toml"
    with path.open("rb") as stream:
        return tomllib.load(stream)


def test_template_describes_phase_twenty_one(config_template: dict[str, Any]) -> None:
    assert config_template["project"]["phase"] == 21
    assert config_template["project"]["name"] == "Professional Halal SMC/ICT Spot Signal Bot"


def test_template_documents_spot_signal_only_intent(config_template: dict[str, Any]) -> None:
    assert config_template["scope"] == {
        "market": "spot",
        "direction": "long_only",
        "signal_only": True,
    }


@pytest.mark.parametrize(
    "setting",
    ["authenticated_exchange_access", "order_execution", "leverage", "margin", "short_selling"],
)
def test_template_keeps_future_capabilities_disabled(
    config_template: dict[str, Any], setting: str
) -> None:
    assert config_template["safety"][setting] is False
