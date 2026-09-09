"""Shared Phase 24B fixtures: real published BUY frames + a real drawing.

Frames and drawings are produced by the *existing* deterministic Phase 1-23
engines so the presentation layer is tested against real upstream models, not
stub stand-ins. Network is never used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.outcome_tracking.helpers import signal_frames

from smcsignal.analysis.halal_filter import AssetClassification
from smcsignal.analysis.signal_eligibility.models import EligibilityStatus, MarketBias
from smcsignal.analysis.signal_engine import SignalDirection, SignalReason, SignalStatus
from smcsignal.delivery.message import SignalMessage


@pytest.fixture(scope="session")
def signal_frames_fixture():
    """The full frozen-chain signal frames (cached once per session)."""
    return signal_frames()


@pytest.fixture(scope="session")
def buy_snapshot(signal_frames_fixture):
    """The first real published BUY_SIGNAL frame from the frozen chain."""
    buys = [frame for frame in signal_frames_fixture if frame.status is SignalStatus.BUY_SIGNAL]
    assert buys, "expected at least one BUY frame from the fixture chain"
    return buys[0]


@pytest.fixture(scope="session")
def non_buy_snapshot(signal_frames_fixture):
    """The first real non-BUY frame (NO_SIGNAL / BEARISH_AVOID)."""
    others = [
        frame for frame in signal_frames_fixture if frame.status is not SignalStatus.BUY_SIGNAL
    ]
    assert others, "expected at least one non-BUY frame from the fixture chain"
    return others[0]


@pytest.fixture(scope="session")
def a_drawing():
    """One real DrawingModel over the same BTCUSDT 15m series."""
    from tests.visualization.helpers import model

    drawing = model()
    assert drawing.symbol == "BTCUSDT"
    assert drawing.timeframe == "15m"
    return drawing


_START = datetime(2024, 1, 1, tzinfo=UTC)
_END = datetime(2024, 1, 1, 1, tzinfo=UTC)


def make_message(
    *,
    status: SignalStatus = SignalStatus.BUY_SIGNAL,
    direction: SignalDirection = SignalDirection.LONG,
    classification: AssetClassification = AssetClassification.HALAL,
    eligibility: EligibilityStatus = EligibilityStatus.ELIGIBLE,
    bias: MarketBias = MarketBias.LONG_BIAS,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    score_total: int = 25,
    publish_threshold: int = 10,
    signal_id: str = "signal-candidate:abc123",
    reasons: tuple[SignalReason, ...] = (SignalReason.HALAL_ASSET, SignalReason.LONG_BIAS),
    reference_price: Decimal | None = Decimal("24"),
) -> SignalMessage:
    """A controlled SignalMessage projection (BUY by default) for unit tests."""
    return SignalMessage(
        signal_id=signal_id,
        symbol=symbol,
        timeframe=timeframe,
        status=status,
        direction=direction,
        classification=classification,
        eligibility_status=eligibility,
        bias=bias,
        score_total=score_total,
        publish_threshold=publish_threshold,
        setup_identity="spot-setup:xyz",
        reasons=reasons,
        closed_at=_END,
        published_at=_END,
        evidence_available_at=_START,
        reference_price=reference_price,
        explanation="descriptive facts; not advice",
        provenance_ids=("eligibility-frame:1",),
    )
