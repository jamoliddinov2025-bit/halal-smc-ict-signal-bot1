"""Phase 24C orchestration tests: offline end-to-end delivery coordinator.

These run the full offline pipeline over *real* upstream frames and a real
DrawingModel. No network, no secrets, no trading surface, no Phase 1-23 change.
"""

from __future__ import annotations

import dataclasses

import pytest

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.signal_engine import SignalStatus
from smcsignal.delivery import (
    DeliveryBatchResult,
    DeliveryCoordinator,
    DeliveryOutcome,
    DeliveryRegistry,
    DeliveryState,
    FailureCategory,
    FakeTransportSink,
    NullSink,
    OrchestrationConfig,
    load_orchestration_config,
)


@pytest.fixture(scope="module")
def buys(signal_frames_fixture):
    """The published BUY frames from the frozen chain (order preserved)."""
    return tuple(
        frame for frame in signal_frames_fixture if frame.status is SignalStatus.BUY_SIGNAL
    )


def _fresh_coordinator(outcomes=None, **kwargs):
    sink = FakeTransportSink() if outcomes is None else FakeTransportSink([outcomes])
    return DeliveryCoordinator(sink, **kwargs), sink


def _mismatched_drawing(a_drawing):
    return dataclasses.replace(a_drawing, symbol="ETHUSDT")


# --- pipeline + context binding -------------------------------------------------


def test_deliver_buy_end_to_end(buy_snapshot) -> None:
    coordinator, sink = _fresh_coordinator()
    outcome = coordinator.deliver(buy_snapshot, "channel-buys")
    assert isinstance(outcome, DeliveryOutcome)
    assert outcome.state is DeliveryState.DELIVERED
    assert outcome.signal_id == buy_snapshot.signal_id
    assert outcome.delivery_id.startswith("delivery:")
    assert outcome.message_id.startswith("message:")
    assert len(sink.seen) == 1
    # context bound to real upstream facts
    assert outcome.context.symbol == "BTCUSDT"
    assert outcome.context.timeframe == "15m"
    assert outcome.context.reference_price is not None
    assert outcome.context.explanation_present is True
    # no invented trading facts in the rendered text
    lowered = outcome.caption.lower()
    for token in ("stop-loss", "take-profit", "entry ", "target ", "probability"):
        assert token not in lowered


def test_only_buy_signal_enters_delivery_path(non_buy_snapshot) -> None:
    coordinator, sink = _fresh_coordinator()
    with pytest.raises(AnalysisInputError):
        coordinator.deliver(non_buy_snapshot, "channel-buys")
    assert len(sink.seen) == 0  # nothing was ever sent


def test_buy_snapshot_is_not_mutated(buy_snapshot) -> None:
    signal_id = buy_snapshot.signal_id
    status = buy_snapshot.status
    coordinator, _ = _fresh_coordinator()
    coordinator.deliver(buy_snapshot, "channel-buys")
    coordinator.deliver(buy_snapshot, "channel-buys")
    assert buy_snapshot.signal_id == signal_id
    assert buy_snapshot.status is status


# --- determinism -----------------------------------------------------------------


def test_identical_input_produces_identical_outcome(buy_snapshot) -> None:
    coordinator_a, _ = _fresh_coordinator()
    coordinator_b, _ = _fresh_coordinator()
    a = coordinator_a.deliver(buy_snapshot, "channel-buys")
    b = coordinator_b.deliver(buy_snapshot, "channel-buys")
    assert a.caption == b.caption
    assert a.delivery_id == b.delivery_id
    assert a.message_id == b.message_id
    assert a.context == b.context
    assert a.state is b.state


# --- dedup + states --------------------------------------------------------------


def test_repeated_input_is_deduplicated(buy_snapshot) -> None:
    coordinator, sink = _fresh_coordinator()
    first = coordinator.deliver(buy_snapshot, "channel-buys")
    second = coordinator.deliver(buy_snapshot, "channel-buys")
    assert first.state is DeliveryState.DELIVERED
    assert second.state is DeliveryState.SKIPPED_DUPLICATE
    assert second.delivery_id == first.delivery_id
    assert len(sink.seen) == 1  # a second send was never attempted


def test_distinct_signals_are_independent(buys) -> None:
    coordinator, sink = _fresh_coordinator()
    a, b = buys[0], buys[1]
    oa = coordinator.deliver(a, "channel-buys")
    ob = coordinator.deliver(b, "channel-buys")
    assert oa.state is DeliveryState.DELIVERED
    assert ob.state is DeliveryState.DELIVERED
    assert oa.signal_id != ob.signal_id
    assert oa.delivery_id != ob.delivery_id
    assert len(sink.seen) == 2


def test_disable_deduplication_reenables_send(buy_snapshot) -> None:
    config = OrchestrationConfig(deduplicate=False)
    coordinator, sink = _fresh_coordinator(config=config)
    first = coordinator.deliver(buy_snapshot, "channel-buys")
    second = coordinator.deliver(buy_snapshot, "channel-buys")
    assert first.state is DeliveryState.DELIVERED
    assert second.state is DeliveryState.DELIVERED  # dedup off => sent again
    assert second.delivery_id == first.delivery_id  # same logical delivery
    assert len(sink.seen) == 2


def test_sent_and_unknown_and_retry_states(buy_snapshot) -> None:
    # SENT
    sink = FakeTransportSink([DeliveryState.SENT])
    sent = DeliveryCoordinator(sink).deliver(buy_snapshot, "channel-buys")
    assert sent.state is DeliveryState.SENT
    # UNKNOWN is surfaced, never retried silently
    sink = FakeTransportSink([DeliveryState.UNKNOWN], failure_category=FailureCategory.TIMEOUT)
    unknown = DeliveryCoordinator(sink, config=OrchestrationConfig(max_attempts=3)).deliver(
        buy_snapshot, "channel-buys"
    )
    assert unknown.state is DeliveryState.UNKNOWN
    assert len(sink.seen) == 1
    # FAILED retried up to max_attempts then surfaces terminal FAILED
    sink = FakeTransportSink([DeliveryState.FAILED], failure_category=FailureCategory.NETWORK)
    failed = DeliveryCoordinator(sink, config=OrchestrationConfig(max_attempts=2)).deliver(
        buy_snapshot, "channel-buys"
    )
    assert failed.state is DeliveryState.FAILED
    assert len(sink.seen) == 2
    # NullSink -> NOT_ATTEMPTED
    null = NullSink()
    not_attempted = DeliveryCoordinator(null).deliver(buy_snapshot, "channel-buys")
    assert not_attempted.state is DeliveryState.NOT_ATTEMPTED


def test_disabled_coordinator_is_noop_not_attempted(buy_snapshot) -> None:
    config = OrchestrationConfig(enabled=False)
    coordinator, sink = _fresh_coordinator(config=config)
    outcome = coordinator.deliver(buy_snapshot, "channel-buys")
    assert outcome.state is DeliveryState.NOT_ATTEMPTED
    assert outcome.failure_category is FailureCategory.CONFIG
    assert len(sink.seen) == 0


def test_shared_registry_deduplicates_across_coordinators(buy_snapshot) -> None:
    registry = DeliveryRegistry()
    coordinator_a, _ = _fresh_coordinator(registry=registry)
    coordinator_b, _ = _fresh_coordinator(registry=registry)
    assert coordinator_a.deliver(buy_snapshot, "channel-buys").state is DeliveryState.DELIVERED
    assert coordinator_b.deliver(buy_snapshot, "channel-buys").state is (
        DeliveryState.SKIPPED_DUPLICATE
    )


# --- chart orchestration ---------------------------------------------------------


def test_matching_chart_is_bound_to_signal(buy_snapshot, a_drawing) -> None:
    coordinator, _ = _fresh_coordinator()
    outcome = coordinator.deliver(buy_snapshot, "channel-buys", drawing=a_drawing)
    assert outcome.chart_present is True
    assert outcome.chart is not None
    assert outcome.chart.signal_id == buy_snapshot.signal_id
    assert outcome.chart.drawing_id == a_drawing.drawing_id
    assert outcome.context.drawing_id == a_drawing.drawing_id


def test_chart_failure_does_not_invalidate_text_delivery(buy_snapshot, a_drawing) -> None:
    wrong = _mismatched_drawing(a_drawing)
    coordinator, sink = _fresh_coordinator()
    outcome = coordinator.deliver(buy_snapshot, "channel-buys", drawing=wrong)
    # the mismatched chart is dropped, but the valid text still delivers
    assert outcome.chart_present is False
    assert outcome.context.drawing_id is None
    assert outcome.state is DeliveryState.DELIVERED
    assert len(sink.seen) == 1
    assert "reference close :" in outcome.caption
    lowered = outcome.caption.lower()
    for token in ("stop-loss", "take-profit"):
        assert token not in lowered


# --- batch behaviour -------------------------------------------------------------


def test_batch_preserves_order_and_counts(buys) -> None:
    coordinator, sink = _fresh_coordinator()
    selected = (buys[0], buys[1], buys[0])
    result = coordinator.deliver_many(selected, "channel-buys")
    assert isinstance(result, DeliveryBatchResult)
    assert [o.signal_id for o in result.outcomes] == [s.signal_id for s in selected]
    assert [o.state for o in result.outcomes] == [
        DeliveryState.DELIVERED,
        DeliveryState.DELIVERED,
        DeliveryState.SKIPPED_DUPLICATE,
    ]
    assert result.delivered() == 2
    assert result.skipped_duplicates() == 1
    assert len(sink.seen) == 2  # exactly the non-duplicate sends


def test_batch_rejects_non_signal_inputs(buy_snapshot) -> None:
    coordinator, _ = _fresh_coordinator()
    with pytest.raises(AnalysisInputError):
        coordinator.deliver_many((buy_snapshot, object()), "channel-buys")


def test_no_cross_signal_contamination_and_no_lookahead(buys) -> None:
    a, b = buys[0], buys[1]
    # A single outcome must equal its outcome when delivered alone (no lookahead)
    solo_a = DeliveryCoordinator(FakeTransportSink()).deliver(a, "channel-buys")
    solo_b = DeliveryCoordinator(FakeTransportSink()).deliver(b, "channel-buys")
    # Reverse batch order must not change a given signal's own outcome.
    coord, _ = _fresh_coordinator()
    result = coord.deliver_many((b, a), "channel-buys")
    assert result.outcomes[0].delivery_id == solo_b.delivery_id
    assert result.outcomes[0].caption == solo_b.caption
    assert result.outcomes[1].delivery_id == solo_a.delivery_id
    assert result.outcomes[1].caption == solo_a.caption


# --- config ----------------------------------------------------------------------


def test_orchestration_config_validation() -> None:
    with pytest.raises(AnalysisConfigurationError):
        OrchestrationConfig(max_attempts=0)
    with pytest.raises(AnalysisConfigurationError):
        OrchestrationConfig(enabled="yes")  # type: ignore[arg-type]
    with pytest.raises(AnalysisConfigurationError):
        OrchestrationConfig(deduplicate=1)  # type: ignore[arg-type]


def test_load_orchestration_config_round_trip_and_strict(tmp_path) -> None:
    path = tmp_path / "orchestration.toml"
    path.write_text(
        "[orchestration]\nenabled = true\nmax_attempts = 2\ndeduplicate = true\n", encoding="utf-8"
    )
    cfg = load_orchestration_config(path)
    assert cfg.enabled is True
    assert cfg.max_attempts == 2
    assert cfg.deduplicate is True
    bad = tmp_path / "bad.toml"
    bad.write_text("[orchestration]\nenabled = true\nstrategy_threshold = 5\n", encoding="utf-8")
    with pytest.raises(AnalysisConfigurationError):
        load_orchestration_config(bad)
