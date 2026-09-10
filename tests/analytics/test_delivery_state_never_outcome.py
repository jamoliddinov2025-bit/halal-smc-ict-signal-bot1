"""Phase 26A regression: DeliveryState is transport, never a trade outcome.

Telegram delivery success/failure describes what a sink reported about a
message, not what the market did. These tests pin that boundary three ways:
the analytics source never references the delivery layer, delivery receipts
cannot enter the outcome ledger, and a DELIVERED publication keeps its outcome
OPEN with zero WIN statistics.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analytics import AnalyticsObserver
from smcsignal.delivery import DeliveryConfig, DeliveryCoordinator, DeliveryState
from smcsignal.delivery.sink import FakeTransportSink
from tests.analytics.helpers import buy_frames, observed_engine, publish

ANALYTICS_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analytics"


def _analytics_trees() -> list[tuple[str, ast.Module]]:
    return [
        (path.name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(ANALYTICS_ROOT.rglob("*.py"))
    ]


def test_analytics_source_never_references_the_delivery_layer() -> None:
    for name, tree in _analytics_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                assert not module.startswith("smcsignal.delivery"), (
                    f"{name} imports {module}: delivery state must never reach analytics"
                )
            for module in modules:
                assert "DeliveryState" not in module
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id != "DeliveryState", f"{name} names DeliveryState"
            if isinstance(node, ast.Attribute):
                assert node.attr != "DeliveryState", f"{name} reads DeliveryState"


def test_delivered_receipt_never_becomes_a_win() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    buys = buy_frames(frames)
    assert buys, "the fixture publishes real BUY signals"

    coordinator = DeliveryCoordinator(FakeTransportSink([DeliveryState.DELIVERED]))
    receipts = [coordinator.deliver(frame, "destination:phase26-regression") for frame in buys]
    assert all(receipt.state is DeliveryState.DELIVERED for receipt in receipts)

    stats = observer.strategy_stats
    assert stats.total_buy_signals == len(buys)
    assert stats.open_count == len(buys)
    assert stats.finalized_count == 0
    assert stats.win_count == 0
    assert stats.loss_count == 0
    assert stats.flat_count == 0
    assert stats.win_rate is None
    assert all(outcome.status.value == "OPEN" for outcome in observer.open_outcomes)


def test_failed_or_unknown_delivery_never_becomes_a_loss() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    first_buy = buy_frames(frames)[0]

    for state in (DeliveryState.FAILED, DeliveryState.UNKNOWN, DeliveryState.NOT_ATTEMPTED):
        coordinator = DeliveryCoordinator(FakeTransportSink([state]))
        receipt = coordinator.deliver(first_buy, "destination:phase26-regression")
        assert receipt.state is state

    stats = observer.strategy_stats
    assert stats.finalized_count == 0
    assert stats.loss_count == 0
    assert observer.observations[0].outcome.status.value == "OPEN"


def test_delivery_records_cannot_enter_the_outcome_ledger() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    first_buy = buy_frames(frames)[0]
    coordinator = DeliveryCoordinator(FakeTransportSink([DeliveryState.DELIVERED]))
    delivery_outcome = coordinator.deliver(
        first_buy, "destination:phase26-regression", config=DeliveryConfig()
    )
    assert delivery_outcome.state is DeliveryState.DELIVERED

    with pytest.raises(AnalysisInputError, match="Phase 18 SignalOutcome"):
        observer.record_finalized(delivery_outcome)  # type: ignore[arg-type]
    assert observer.strategy_stats.finalized_count == 0
