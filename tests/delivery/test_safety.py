"""Phase 24B safety tests: no signal mutation, no invented facts, halal boundary."""

from __future__ import annotations

import pytest
from tests.delivery.conftest import make_message

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter import AssetClassification
from smcsignal.analysis.signal_eligibility.models import EligibilityStatus, MarketBias
from smcsignal.analysis.signal_engine import SignalDirection, SignalReason, SignalStatus
from smcsignal.delivery import (
    DeliveryConfig,
    prepare_signal_message,
    render_message,
    require_buy_signal,
)


def test_signal_is_unchanged_after_rendering(buy_snapshot) -> None:
    before = buy_snapshot
    prepare_signal_message(before)
    prepare_signal_message(before)
    assert buy_snapshot is before  # immutable object identity preserved
    assert buy_snapshot.status is SignalStatus.BUY_SIGNAL
    assert buy_snapshot.candidate.signal.classification is AssetClassification.HALAL


def test_halal_classification_is_never_modified(buy_snapshot) -> None:
    message = prepare_signal_message(buy_snapshot)
    # The upstream BUY invariant holds: classification is HALAL and is displayed
    # verbatim, never re-classified, inferred, or "corrected".
    assert "classification : HALAL" in message.caption
    assert buy_snapshot.candidate.signal.classification is AssetClassification.HALAL


def test_non_buy_real_frame_is_not_converted(non_buy_snapshot) -> None:
    assert non_buy_snapshot.status is not SignalStatus.BUY_SIGNAL
    with pytest.raises(AnalysisInputError):
        require_buy_signal(non_buy_snapshot)


def test_avoid_record_not_rendered_as_buy() -> None:
    message = make_message(
        status=SignalStatus.BEARISH_AVOID,
        direction=SignalDirection.NONE,
        classification=AssetClassification.HARAM,
        eligibility=EligibilityStatus.ELIGIBLE,
        bias=MarketBias.SHORT_BIAS,
        reasons=(SignalReason.BEARISH_SPOT_AVOID,),
    )
    caption = render_message(message, DeliveryConfig())
    assert "AVOID" in caption
    assert "BUY" not in caption
    assert "LONG" not in caption


def test_no_signal_and_unknown_halal_never_presented_as_buy() -> None:
    message = make_message(
        status=SignalStatus.NO_SIGNAL,
        direction=SignalDirection.NONE,
        classification=AssetClassification.UNKNOWN,
        eligibility=EligibilityStatus.NOT_ELIGIBLE,
        bias=MarketBias.NEUTRAL,
        reasons=(SignalReason.MISSING_REQUIRED_EVIDENCE,),
    )
    caption = render_message(message, DeliveryConfig())
    assert "BUY" not in caption
    assert "LONG" not in caption
    assert "UNKNOWN" in caption  # classification is displayed verbatim


def test_no_invented_signal_fields_in_model() -> None:
    from smcsignal.delivery.message import SignalMessage

    for banned in (
        "entry_price",
        "stop_loss",
        "take_profit",
        "target_price",
        "probability",
        "confidence",
        "position_size",
    ):
        assert not hasattr(SignalMessage, banned)


def test_render_never_emits_order_or_sl_tp_terms(buy_snapshot) -> None:
    caption = prepare_signal_message(buy_snapshot).caption.lower()
    # No invented execution guidance: no actionable SL/TP/order-entry vocabulary.
    # (The verbatim upstream WHY text legitimately names analytical building
    # blocks such as "order_block"; that is not an execution instruction.)
    for token in (
        "stop-loss",
        "take-profit",
        "buy order",
        "sell order",
        "entry price",
        "place order",
        "position size",
        "slippage",
        "set a stop",
    ):
        assert token not in caption


def test_buy_message_is_halal_and_eligible(buy_snapshot) -> None:
    message = prepare_signal_message(buy_snapshot)
    assert "classification : HALAL" in message.caption
    assert "ELIGIBLE" in message.caption
